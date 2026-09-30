#!/usr/bin/env bash
# Testes do docker/kb_assets/publish.sh usando volume Docker e nginx temporários (sem mocks).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUBLISH="${SCRIPT_DIR}/publish.sh"
CONF_FILE="${SCRIPT_DIR}/default.conf"
TEST_VOLUME="kb_assets_test_$$"
CONTAINER_NAME="kb_assets_publish_test_$$"
WORK_DIR="$(mktemp -d)"
SAMPLE_PNG="${WORK_DIR}/sample.png"
FAILURES=0

# PNG 1x1 gerado no próprio teste (os manuais em kb/ não são versionados)
base64 -d > "${SAMPLE_PNG}" <<<'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='

# cleanup: remove container, volume e arquivos temporários dos testes.
# Entrada: nenhuma. Saída: nenhuma.
cleanup() {
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  docker volume rm "${TEST_VOLUME}" >/dev/null 2>&1 || true
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

# check: registra o resultado de uma asserção.
# Entrada: $1 descrição, $2 código de retorno da asserção (0 = passou). Saída: incrementa FAILURES se falhou.
check() {
  if [[ "$2" == "0" ]]; then
    echo "ok   - $1"
  else
    echo "FAIL - $1"
    FAILURES=$((FAILURES + 1))
  fi
}

# make_manual: cria uma pasta de manual de teste com README, um .md e duas imagens.
# Entrada: $1 caminho da pasta. Saída: arquivos criados em $1.
make_manual() {
  local dir="$1"
  mkdir -p "${dir}/images"
  cp "${SAMPLE_PNG}" "${dir}/images/a.png"
  cp "${SAMPLE_PNG}" "${dir}/images/b.png"
  printf '# Leia-me\n\n![x](images/a.png)\n' > "${dir}/README.md"
  cat > "${dir}/manual.md" <<'EOF'
# Manual

## Passo 1

Texto com images/a.png fora de link.

![Passo 1: tela (inicial)](images/a.png)

![Passo 2](./images/b.png)

![Externa](https://example.com/images/c.png)
EOF
}

# http_status: devolve o status HTTP de uma URL.
# Entrada: $1 URL. Saída: código HTTP em stdout.
http_status() {
  curl -s -o /dev/null -w '%{http_code}' -m 5 "$1"
}

docker volume create "${TEST_VOLUME}" >/dev/null
docker run -d --name "${CONTAINER_NAME}" -p 127.0.0.1::80 \
  -v "${CONF_FILE}:/etc/nginx/conf.d/default.conf:ro" \
  -v "${TEST_VOLUME}:/usr/share/nginx/html/kb-assets:ro" \
  nginx:alpine >/dev/null
PORT="$(docker port "${CONTAINER_NAME}" 80/tcp | head -1 | cut -d: -f2)"
BASE="http://127.0.0.1:${PORT}/kb-assets"
for _ in $(seq 1 20); do curl -s -o /dev/null "${BASE}/" && break; sleep 0.25; done
export KB_ASSETS_VOLUME="${TEST_VOLUME}" KB_ASSETS_BASE_URL="${BASE}/"

# test_happy_path: publica um manual válido e confere volume, Markdown gerado e original intacto.
# Entrada: nenhuma. Saída: registra asserções via check.
test_happy_path() {
  local dir="${WORK_DIR}/happy" out original_sum
  make_manual "${dir}"
  original_sum="$(sha256sum "${dir}/manual.md" | cut -d' ' -f1)"
  "${PUBLISH}" "${dir}" demo >/dev/null 2>&1
  check "happy: código de saída 0" "$?"
  out="${dir}/build/manual.dify.md"
  [[ -f "${out}" ]]; check "happy: build/manual.dify.md gerado" "$?"
  grep -qF "![Passo 1: tela (inicial)](${BASE}/demo/a.png)" "${out}"; check "happy: images/ reescrito" "$?"
  grep -qF "![Passo 2](${BASE}/demo/b.png)" "${out}"; check "happy: ./images/ reescrito" "$?"
  grep -qF "](https://example.com/images/c.png)" "${out}"; check "happy: URL externa preservada" "$?"
  grep -qF "Texto com images/a.png fora de link." "${out}"; check "happy: texto fora de link preservado" "$?"
  ! grep -qE '\]\((\./)?images/' "${out}"; check "happy: nenhuma referência relativa restante" "$?"
  [[ "$(http_status "${BASE}/demo/a.png")" == 200 ]]; check "happy: a.png servida" "$?"
  [[ "$(sha256sum "${dir}/manual.md" | cut -d' ' -f1)" == "${original_sum}" ]]; check "happy: .md original intacto" "$?"
}

# test_republish_removes_stale_images: republica sem b.png e confere que ela sai do ar.
# Entrada: nenhuma. Saída: registra asserções via check.
test_republish_removes_stale_images() {
  local dir="${WORK_DIR}/stale"
  make_manual "${dir}"
  "${PUBLISH}" "${dir}" stale >/dev/null 2>&1
  rm "${dir}/images/b.png"
  sed -i '/b\.png/d' "${dir}/manual.md"
  "${PUBLISH}" "${dir}" stale >/dev/null 2>&1
  check "stale: republicação com sucesso" "$?"
  [[ "$(http_status "${BASE}/stale/b.png")" == 404 ]]; check "stale: b.png removida do volume" "$?"
  [[ "$(http_status "${BASE}/stale/a.png")" == 200 ]]; check "stale: a.png continua servida" "$?"
}

# test_fails_when_image_missing: referência a imagem inexistente deve falhar citando o arquivo.
# Entrada: nenhuma. Saída: registra asserções via check.
test_fails_when_image_missing() {
  local dir="${WORK_DIR}/missing" err
  make_manual "${dir}"
  printf '\n![Faltando](images/nao-existe.png)\n' >> "${dir}/manual.md"
  err="$("${PUBLISH}" "${dir}" missing 2>&1 >/dev/null)"
  [[ $? -ne 0 ]]; check "missing: código de saída diferente de 0" "$?"
  grep -qF "nao-existe.png" <<<"${err}"; check "missing: erro cita o arquivo" "$?"
}

# test_rejects_invalid_slug: slugs perigosos ou fora do padrão devem ser recusados.
# Entrada: nenhuma. Saída: registra asserções via check.
test_rejects_invalid_slug() {
  local dir="${WORK_DIR}/slug" slug
  make_manual "${dir}"
  for slug in "../x" "a/b" "Office" "com espaco" "" ".oculto"; do
    "${PUBLISH}" "${dir}" "${slug}" >/dev/null 2>&1
    [[ $? -ne 0 ]]; check "slug: '${slug}' recusado" "$?"
  done
  [[ ! -e "${dir}/build" ]]; check "slug: nada gerado com slug inválido" "$?"
}

# test_fails_when_volume_missing: volume inexistente deve falhar sem criá-lo.
# Entrada: nenhuma. Saída: registra asserções via check.
test_fails_when_volume_missing() {
  local dir="${WORK_DIR}/novolume" volume="kb_assets_inexistente_$$"
  make_manual "${dir}"
  KB_ASSETS_VOLUME="${volume}" "${PUBLISH}" "${dir}" demo >/dev/null 2>&1
  [[ $? -ne 0 ]]; check "volume: código de saída diferente de 0" "$?"
  ! docker volume inspect "${volume}" >/dev/null 2>&1; check "volume: não foi criado" "$?"
}

# test_rejects_ambiguous_markdown: pasta com mais de um .md (além do README) deve falhar.
# Entrada: nenhuma. Saída: registra asserções via check.
test_rejects_ambiguous_markdown() {
  local dir="${WORK_DIR}/ambiguous"
  make_manual "${dir}"
  printf '# Outro\n' > "${dir}/outro.md"
  "${PUBLISH}" "${dir}" demo >/dev/null 2>&1
  [[ $? -ne 0 ]]; check "ambíguo: dois .md recusados" "$?"
}

# test_rejects_unsafe_image_names: nomes de imagem com espaço/acentos devem ser recusados.
# Entrada: nenhuma. Saída: registra asserções via check.
test_rejects_unsafe_image_names() {
  local dir="${WORK_DIR}/unsafe" err
  make_manual "${dir}"
  cp "${SAMPLE_PNG}" "${dir}/images/tela ação.png"
  printf '\n![Ruim](images/tela ação.png)\n' >> "${dir}/manual.md"
  err="$("${PUBLISH}" "${dir}" unsafe 2>&1 >/dev/null)"
  [[ $? -ne 0 ]]; check "nome inseguro: recusado" "$?"
  grep -qF "tela ação.png" <<<"${err}"; check "nome inseguro: erro cita o arquivo" "$?"
}

# test_publishes_only_referenced_images: imagem não referenciada no .md não pode ser publicada.
# Entrada: nenhuma. Saída: registra asserções via check.
test_publishes_only_referenced_images() {
  local dir="${WORK_DIR}/onlyref"
  make_manual "${dir}"
  cp "${SAMPLE_PNG}" "${dir}/images/rascunho.png"
  "${PUBLISH}" "${dir}" onlyref >/dev/null 2>&1
  check "referenciadas: publicação com sucesso" "$?"
  [[ "$(http_status "${BASE}/onlyref/a.png")" == 200 ]]; check "referenciadas: a.png servida" "$?"
  [[ "$(http_status "${BASE}/onlyref/b.png")" == 200 ]]; check "referenciadas: b.png servida" "$?"
  [[ "$(http_status "${BASE}/onlyref/rascunho.png")" == 404 ]]; check "referenciadas: rascunho.png não publicada" "$?"
}

# run_interactive: roda o publish.sh sem KB_ASSETS_BASE_URL, com respostas via stdin.
# Entrada: $1 pasta, $2 slug, $3 respostas (printf-format, ex.: 'a\n\ns\n'). Saída: stdout/stderr do script; retorna seu código.
run_interactive() {
  printf "$3" | env -u KB_ASSETS_BASE_URL "${PUBLISH}" "$1" "$2"
}

# test_prompt_happy_path: fluxo interativo completo com domínio, protocolo padrão e confirmação.
# Entrada: nenhuma. Saída: registra asserções via check.
test_prompt_happy_path() {
  local dir="${WORK_DIR}/prompt_ok" out
  make_manual "${dir}"
  out="$(run_interactive "${dir}" promptok "127.0.0.1:${PORT}\n\ns\n" 2>/dev/null)"
  check "prompt: código de saída 0" "$?"
  grep -qF "](http://127.0.0.1:${PORT}/kb-assets/promptok/a.png)" "${dir}/build/manual.dify.md"; check "prompt: build com URL do domínio informado" "$?"
  [[ "$(http_status "${BASE}/promptok/a.png")" == 200 ]]; check "prompt: a.png servida" "$?"
  ! grep -qE 'Domínio do Dify|Protocolo|Confirma' <<<"${out}"; check "prompt: stdout sem o texto das perguntas" "$?"
}

# test_prompt_rejects_invalid_domain: domínios inválidos devem ser recusados sem gerar build/.
# Entrada: nenhuma. Saída: registra asserções via check.
test_prompt_rejects_invalid_domain() {
  local dir="${WORK_DIR}/prompt_dom" domain
  make_manual "${dir}"
  for domain in "http://x.y" "dominio invalido" "a/b" ""; do
    run_interactive "${dir}" promptdom "${domain}\n\ns\n" >/dev/null 2>&1
    [[ $? -ne 0 ]]; check "domínio: '${domain}' recusado" "$?"
  done
  [[ ! -e "${dir}/build" ]]; check "domínio: nada gerado" "$?"
}

# test_prompt_rejects_invalid_protocol: protocolo diferente de http/https deve ser recusado.
# Entrada: nenhuma. Saída: registra asserções via check.
test_prompt_rejects_invalid_protocol() {
  local dir="${WORK_DIR}/prompt_proto"
  make_manual "${dir}"
  run_interactive "${dir}" promptproto "127.0.0.1:${PORT}\nftp\ns\n" >/dev/null 2>&1
  [[ $? -ne 0 ]]; check "protocolo: 'ftp' recusado" "$?"
  [[ ! -e "${dir}/build" ]]; check "protocolo: nada gerado" "$?"
}

# test_prompt_cancel: responder 'n' na confirmação cancela sem tocar no volume.
# Entrada: nenhuma. Saída: registra asserções via check.
test_prompt_cancel() {
  local dir="${WORK_DIR}/prompt_cancel" err
  make_manual "${dir}"
  err="$(run_interactive "${dir}" promptcancel "127.0.0.1:${PORT}\n\nn\n" 2>&1 >/dev/null)"
  [[ $? -ne 0 ]]; check "cancelar: código de saída diferente de 0" "$?"
  grep -qF "Publicação cancelada" <<<"${err}"; check "cancelar: mensagem em stderr" "$?"
  [[ ! -e "${dir}/build" ]]; check "cancelar: sem build/" "$?"
  [[ "$(http_status "${BASE}/promptcancel/a.png")" == 404 ]]; check "cancelar: volume não tocado" "$?"
}

# test_prompt_without_answers: sem stdin e sem variável, o erro orienta o uso de KB_ASSETS_BASE_URL.
# Entrada: nenhuma. Saída: registra asserções via check.
test_prompt_without_answers() {
  local dir="${WORK_DIR}/prompt_empty" err
  make_manual "${dir}"
  err="$(env -u KB_ASSETS_BASE_URL "${PUBLISH}" "${dir}" promptempty 2>&1 >/dev/null </dev/null)"
  [[ $? -ne 0 ]]; check "sem respostas: código de saída diferente de 0" "$?"
  grep -qF "KB_ASSETS_BASE_URL" <<<"${err}"; check "sem respostas: erro cita KB_ASSETS_BASE_URL" "$?"
}

# test_rejects_invalid_base_url_env: KB_ASSETS_BASE_URL sem esquema http(s) deve ser recusada.
# Entrada: nenhuma. Saída: registra asserções via check.
test_rejects_invalid_base_url_env() {
  local dir="${WORK_DIR}/badenv"
  make_manual "${dir}"
  KB_ASSETS_BASE_URL="dify.dev.dti" "${PUBLISH}" "${dir}" badenv >/dev/null 2>&1
  [[ $? -ne 0 ]]; check "base url: sem esquema recusada" "$?"
  [[ ! -e "${dir}/build" ]]; check "base url: nada gerado" "$?"
}

# test_rejects_unsupported_image_refs: referências de imagem não suportadas falham antes de publicar.
# Entrada: nenhuma. Saída: registra asserções via check.
test_rejects_unsupported_image_refs() {
  local base="${WORK_DIR}/unsupported" ref expected err dir i=0
  local refs=('<img src="images/a.png">' '![x](imgs/a.png)' '![x](../a.png)' '![x][ref]')
  local needles=('<img' 'imgs/a.png' '../a.png' '![x][ref]')
  for ref in "${refs[@]}"; do
    expected="${needles[$i]}"
    dir="${base}${i}"
    make_manual "${dir}"
    printf '\n%s\n' "${ref}" >> "${dir}/manual.md"
    err="$("${PUBLISH}" "${dir}" "unsup${i}" 2>&1 >/dev/null)"
    [[ $? -ne 0 ]]; check "não suportada: '${ref}' recusada" "$?"
    grep -qF "${expected}" <<<"${err}"; check "não suportada: erro cita '${expected}'" "$?"
    [[ ! -e "${dir}/build" ]]; check "não suportada: '${ref}' sem build/" "$?"
    [[ "$(http_status "${BASE}/unsup${i}/a.png")" == 404 ]]; check "não suportada: '${ref}' volume intacto" "$?"
    i=$((i + 1))
  done
}

test_happy_path
test_republish_removes_stale_images
test_fails_when_image_missing
test_rejects_invalid_slug
test_fails_when_volume_missing
test_rejects_ambiguous_markdown
test_rejects_unsafe_image_names
test_publishes_only_referenced_images
test_prompt_happy_path
test_prompt_rejects_invalid_domain
test_prompt_rejects_invalid_protocol
test_prompt_cancel
test_prompt_without_answers
test_rejects_invalid_base_url_env
test_rejects_unsupported_image_refs

if (( FAILURES > 0 )); then
  echo "${FAILURES} teste(s) falharam"
  exit 1
fi
echo "Todos os testes passaram"
