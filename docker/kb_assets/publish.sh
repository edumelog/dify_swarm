#!/usr/bin/env bash
# Publica as imagens de um manual da base de conhecimento no volume do serviço
# kb_assets e gera a versão do Markdown com URLs absolutas para ingestão no Dify.
#
# Uso: docker/kb_assets/publish.sh <pasta-do-manual> <slug>
# Variáveis: KB_ASSETS_VOLUME, KB_ASSETS_BASE_URL, KB_HELPER_IMAGE
set -euo pipefail

KB_ASSETS_VOLUME="${KB_ASSETS_VOLUME:-dify_dify_kb_assets}"
KB_ASSETS_BASE_URL="${KB_ASSETS_BASE_URL:-http://dify.dev.dti/kb-assets}"
KB_HELPER_IMAGE="${KB_HELPER_IMAGE:-busybox:latest}"
SLUG_PATTERN='^[a-z0-9][a-z0-9-]*$'
IMAGE_NAME_PATTERN='^[A-Za-z0-9._-]+$'

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

# validate_image_refs: confere se cada referência tem nome seguro e existe em images/.
# Entrada: $1 pasta images/, $2 arquivo .md. Saída: nenhuma; encerra com erro listando os problemas.
validate_image_refs() {
  local images_dir="$1" markdown="$2" name problems=()
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

# copy_images_to_volume: substitui o conteúdo de /<slug>/ no volume pelas imagens do manual.
# A cópia é feita em diretório oculto temporário e trocada no final, sem deixar órfãos.
# Entrada: $1 pasta images/ (absoluta), $2 slug. Saída: nenhuma; encerra com erro se o volume não existir.
copy_images_to_volume() {
  local images_dir="$1" slug="$2"
  docker volume inspect "${KB_ASSETS_VOLUME}" >/dev/null 2>&1 \
    || fail "volume '${KB_ASSETS_VOLUME}' não existe. Faça o deploy da stack 'dify' antes de publicar."
  docker run --rm \
    -v "${KB_ASSETS_VOLUME}:/dst" \
    -v "${images_dir}:/src:ro" \
    "${KB_HELPER_IMAGE}" sh -c '
      set -e
      tmp="/dst/.$1.tmp"
      rm -rf "$tmp"
      mkdir -p "$tmp"
      cp -r /src/. "$tmp/"
      chmod -R a+rX "$tmp"
      rm -rf "/dst/$1"
      mv "$tmp" "/dst/$1"
    ' sh "${slug}"
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
    echo "Verifique o serviço dify_kb_assets e a custom location /kb-assets/ no NGPM." >&2
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

  slug_url="${KB_ASSETS_BASE_URL%/}/${slug}"
  target="${manual_dir}/build/$(basename "${markdown}" .md).dify.md"

  copy_images_to_volume "${images_dir}" "${slug}"
  write_dify_markdown "${markdown}" "${target}" "${slug_url}"
  verify_urls "${slug_url}" "${markdown}"

  count="$(list_image_refs "${markdown}" | grep -c . || true)"
  echo "Publicadas as imagens de '${manual_dir}' em ${slug_url}/ (${count} referenciadas)."
  echo "Markdown para o Dify: ${target}"
}

main "$@"
