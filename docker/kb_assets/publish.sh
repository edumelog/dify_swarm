#!/usr/bin/env bash
# Publica as imagens de um manual da base de conhecimento no volume do serviço
# kb_assets e gera a versão do Markdown com URLs absolutas para ingestão no Dify.
#
# Uso: docker/kb_assets/publish.sh <pasta-do-manual> <slug>
# Variáveis: STACK_NAME (padrão: dify; define o volume <stack>_dify_kb_assets), KB_ASSETS_VOLUME,
# KB_HELPER_IMAGE e, opcionalmente, KB_ASSETS_BASE_URL
# (ex.: http://dify.dev.dti/kb-assets). Sem KB_ASSETS_BASE_URL, o domínio é perguntado.
set -euo pipefail

KB_ASSETS_VOLUME="${KB_ASSETS_VOLUME:-${STACK_NAME:-dify}_dify_kb_assets}"
KB_ASSETS_BASE_URL="${KB_ASSETS_BASE_URL:-}"
KB_ASSETS_PATH="/kb-assets"
KB_HELPER_IMAGE="${KB_HELPER_IMAGE:-busybox:latest}"
SLUG_PATTERN='^[a-z0-9][a-z0-9-]*$'
IMAGE_NAME_PATTERN='^[A-Za-z0-9._-]+$'
HOST_PATTERN='[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:[0-9]{1,5})?'
DOMAIN_PATTERN="^${HOST_PATTERN}\$"
BASE_URL_PATTERN="^https?://${HOST_PATTERN}(/[^[:space:]]*)?\$"

# fail: escreve uma mensagem de erro em stderr e encerra com código 1.
# Entrada: $* mensagem. Saída: nenhuma (encerra o script).
fail() {
  echo "ERRO: $*" >&2
  exit 1
}

# validate_slug: garante que o slug é seguro para uso como diretório e URL.
# Entrada: $1 slug. Saída: nenhuma; encerra com erro se inválido.
validate_slug() {
  [[ "$1" =~ ${SLUG_PATTERN} ]] \
    || fail "slug inválido '$1': use apenas letras minúsculas, números e hífen (ex.: office365)."
}

# find_manual_markdown: localiza o único .md do manual (ignorando README.md).
# Entrada: $1 pasta do manual. Saída: caminho do .md em stdout; encerra com erro se houver 0 ou mais de 1.
find_manual_markdown() {
  local files=()
  mapfile -t files < <(find "$1" -maxdepth 1 -type f -name '*.md' ! -name 'README.md' | sort)
  (( ${#files[@]} == 1 )) \
    || fail "a pasta '$1' deve conter exatamente um arquivo .md além do README.md (encontrados: ${#files[@]})."
  echo "${files[0]}"
}

# list_image_refs: lista os nomes de arquivo referenciados como images/ ou ./images/ no Markdown.
# Entrada: $1 arquivo .md. Saída: um nome de arquivo por linha em stdout (sem repetição).
list_image_refs() {
  grep -oE '\]\((\./)?images/[^)]+\)' "$1" \
    | sed -E 's#^\]\((\./)?images/##; s#\)$##' \
    | sort -u || true
}

# find_unsupported_image_refs: lista imagens do Markdown que o publish.sh não sabe reescrever.
# O texto alternativo pode conter um nível de colchetes (ex.: ![a [b]](imgs/x.png)).
# Entrada: $1 arquivo .md. Saída: uma mensagem (sem o prefixo "ERRO: ") por problema em stdout.
find_unsupported_image_refs() {
  local markdown="$1" match target
  while IFS= read -r match; do
    printf 'imagem em HTML não é suportada (%s); use ![descrição](images/arquivo.png)\n' "${match}"
  done < <(grep -oiE '<img[^>]*>?' "${markdown}" || true)
  while IFS= read -r match; do
    target="${match##*](}"
    target="${target%)}"
    if [[ ! "${target}" =~ ^(\./)?images/ && ! "${target}" =~ ^https?:// ]]; then
      printf 'referência de imagem fora de images/ não suportada (%s); mova o arquivo para images/ e use ![descrição](images/arquivo.png)\n' "${match}"
    fi
  done < <(grep -oE '!\[([^][]|\[[^]]*\])*\]\([^)]*\)' "${markdown}" || true)
  while IFS= read -r match; do
    printf 'imagem estilo referência não suportada (%s); use a forma inline ![descrição](images/arquivo.png)\n' "${match}"
  done < <(grep -oE '!\[([^][]|\[[^]]*\])*\]\[[^]]*\]' "${markdown}" || true)
}

# validate_image_refs: recusa referências não suportadas e confere se cada referência tem nome seguro e existe em images/.
# Entrada: $1 pasta images/, $2 arquivo .md. Saída: nenhuma; encerra com erro listando todos os problemas de uma vez.
validate_image_refs() {
  local images_dir="$1" markdown="$2" name line problems=()
  while IFS= read -r line; do
    [[ -n "${line}" ]] && problems+=("${line}")
  done < <(find_unsupported_image_refs "${markdown}")
  while IFS= read -r name; do
    [[ -z "${name}" ]] && continue
    if [[ ! "${name}" =~ ${IMAGE_NAME_PATTERN} ]]; then
      problems+=("nome inválido (use só letras sem acento, números, '.', '_' e '-'): ${name}")
    elif [[ ! -f "${images_dir}/${name}" ]]; then
      problems+=("imagem referenciada não encontrada em images/: ${name}")
    fi
  done < <(list_image_refs "${markdown}")
  if (( ${#problems[@]} > 0 )); then
    printf 'ERRO: %s\n' "${problems[@]}" >&2
    exit 1
  fi
}

# read_answer: lê uma resposta do stdin exibindo o prompt em stderr.
# Remove \r e espaços nas pontas. Entrada: $1 texto do prompt. Saída: resposta em stdout; encerra com erro se não houver como ler.
read_answer() {
  local answer
  printf '%s' "$1" >&2
  IFS= read -r answer || fail "não foi possível ler a resposta. Para uso não interativo, defina KB_ASSETS_BASE_URL (ex.: KB_ASSETS_BASE_URL=http://dify.dev.dti/kb-assets)."
  answer="${answer//$'\r'/}"
  answer="${answer#"${answer%%[![:space:]]*}"}"
  answer="${answer%"${answer##*[![:space:]]}"}"
  printf '%s\n' "${answer}"
}

# ask_base_url: pergunta domínio e protocolo e pede confirmação.
# Entrada: $1 slug. Saída: URL base (sem slug) em stdout; encerra com erro se inválido ou cancelado.
ask_base_url() {
  local slug="$1" domain protocol confirm
  domain="$(read_answer 'Domínio do Dify (ex.: dify.dev.dti, chat.hmg.dti): ')" || exit 1
  domain="${domain,,}"
  [[ "${domain}" =~ ${DOMAIN_PATTERN} ]] \
    || fail "domínio inválido '${domain}': informe só o nome do domínio, sem protocolo nem barras (ex.: dify.dev.dti)."
  protocol="$(read_answer 'Protocolo [http/https] (padrão: http): ')" || exit 1
  protocol="${protocol,,}"
  protocol="${protocol:-http}"
  [[ "${protocol}" == "http" || "${protocol}" == "https" ]] \
    || fail "protocolo inválido '${protocol}': use http ou https."
  echo "As imagens serão publicadas em ${protocol}://${domain}${KB_ASSETS_PATH}/${slug}/" >&2
  confirm="$(read_answer 'Confirma? [S/n]: ')" || exit 1
  case "${confirm,,}" in
    ""|s|sim) ;;
    *) echo "Publicação cancelada." >&2; exit 1 ;;
  esac
  echo "${protocol}://${domain}${KB_ASSETS_PATH}"
}

# resolve_base_url: define a URL base a partir de KB_ASSETS_BASE_URL ou perguntando ao usuário.
# Entrada: $1 slug. Saída: URL base sem barra final em stdout; encerra com erro se inválida.
resolve_base_url() {
  local base
  if [[ -n "${KB_ASSETS_BASE_URL}" ]]; then
    base="${KB_ASSETS_BASE_URL%/}"
    [[ "${base}" =~ ^([A-Za-z]+)(://.*)$ ]] && base="${BASH_REMATCH[1],,}${BASH_REMATCH[2]}"
    [[ "${base,,}" =~ ${BASE_URL_PATTERN} ]] \
      || fail "KB_ASSETS_BASE_URL inválida '${KB_ASSETS_BASE_URL}': use http:// ou https:// e o domínio (ex.: http://dify.dev.dti/kb-assets)."
    echo "${base}"
  else
    ask_base_url "$1" || exit 1
  fi
}

# copy_images_to_volume: substitui o conteúdo de /<slug>/ no volume pelas imagens referenciadas no .md.
# Só os arquivos citados no Markdown são publicados (rascunhos e outros arquivos de images/ ficam de fora).
# A cópia é feita em diretório oculto temporário e trocada no final, sem deixar órfãos.
# Entrada: $1 pasta images/ (absoluta), $2 slug, $3 arquivo .md. Saída: nenhuma; encerra com erro se o Docker
# não responder ou se o volume não existir.
copy_images_to_volume() {
  local images_dir="$1" slug="$2" markdown="$3" names=()
  docker info >/dev/null 2>&1 \
    || fail "não foi possível falar com o Docker (daemon parado ou sem permissão)."
  docker volume inspect "${KB_ASSETS_VOLUME}" >/dev/null 2>&1 \
    || fail "volume '${KB_ASSETS_VOLUME}' não existe. Faça o deploy da stack 'dify' antes de publicar."
  mapfile -t names < <(list_image_refs "${markdown}")
  docker run --rm \
    -v "${KB_ASSETS_VOLUME}:/dst" \
    -v "${images_dir}:/src:ro" \
    "${KB_HELPER_IMAGE}" sh -c '
      set -e
      slug="$1"
      shift
      tmp="/dst/.$slug.tmp"
      rm -rf "$tmp"
      mkdir -p "$tmp"
      for name in "$@"; do
        cp "/src/$name" "$tmp/$name"
      done
      chmod -R a+rX "$tmp"
      rm -rf "/dst/$slug"
      mv "$tmp" "/dst/$slug"
    ' sh "${slug}" ${names[@]+"${names[@]}"}
}

# write_dify_markdown: gera o .dify.md com images/ e ./images/ trocados pela URL pública.
# Entrada: $1 .md de origem, $2 .md de destino, $3 URL base do slug. Saída: arquivo $2 criado.
write_dify_markdown() {
  local source="$1" target="$2" slug_url="$3"
  mkdir -p "$(dirname "${target}")"
  sed -E "s#\]\((\./)?images/#](${slug_url}/#g" "${source}" > "${target}"
}

# verify_urls: confere que cada imagem publicada responde HTTP 200.
# Entrada: $1 URL base do slug, $2 .md de origem. Saída: nenhuma; encerra com erro listando as falhas.
verify_urls() {
  local slug_url="$1" markdown="$2" name status failures=()
  while IFS= read -r name; do
    [[ -z "${name}" ]] && continue
    status="$(curl -s -o /dev/null -w '%{http_code}' -m 10 "${slug_url}/${name}" || true)"
    [[ "${status}" == "200" ]] || failures+=("${slug_url}/${name} (HTTP ${status})")
  done < <(list_image_refs "${markdown}")
  if (( ${#failures[@]} > 0 )); then
    printf 'ERRO: imagem publicada não acessível: %s\n' "${failures[@]}" >&2
    echo "Verifique o serviço dify_kb_assets, a custom location /kb-assets/ no NGPM e se" >&2
    echo "\"Cache Assets\" está desligado no proxy host do NGPM." >&2
    exit 1
  fi
}

# main: valida entradas, publica as imagens, gera o Markdown e verifica as URLs.
# Entrada: $1 pasta do manual, $2 slug. Saída: resumo em stdout; código 0 em sucesso.
main() {
  (( $# == 2 )) || fail "uso: $0 <pasta-do-manual> <slug>"
  local manual_dir="$1" slug="$2" markdown images_dir slug_url target count
  validate_slug "${slug}"
  [[ -d "${manual_dir}/images" ]] || fail "pasta '${manual_dir}/images' não encontrada."
  manual_dir="$(cd "${manual_dir}" && pwd)"
  images_dir="${manual_dir}/images"
  markdown="$(find_manual_markdown "${manual_dir}")"
  validate_image_refs "${images_dir}" "${markdown}"

  slug_url="$(resolve_base_url "${slug}")" || exit 1
  slug_url="${slug_url}/${slug}"
  target="${manual_dir}/build/$(basename "${markdown}" .md).dify.md"

  copy_images_to_volume "${images_dir}" "${slug}" "${markdown}"
  write_dify_markdown "${markdown}" "${target}" "${slug_url}"
  verify_urls "${slug_url}" "${markdown}"

  count="$(list_image_refs "${markdown}" | grep -c . || true)"
  echo "Publicadas as imagens de '${manual_dir}' em ${slug_url}/ (${count} referenciadas)."
  echo "Markdown para o Dify: ${target}"
}

main "$@"
