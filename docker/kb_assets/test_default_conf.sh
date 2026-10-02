#!/usr/bin/env bash
# Testa docker/kb_assets/default.conf em um container nginx:alpine temporário.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="${SCRIPT_DIR}/default.conf"
WORK_DIR="$(mktemp -d)"
CONTAINER_NAME="kb_assets_conf_test_$$"
FAILURES=0

# cleanup: remove o container e o diretório temporário do teste.
# Entrada: nenhuma. Saída: nenhuma.
cleanup() {
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

# expect_status: faz GET na URL e compara o status HTTP com o esperado.
# Entrada: $1 descrição, $2 status esperado, $3 URL. Saída: incrementa FAILURES se divergir.
expect_status() {
  local description="$1" expected="$2" url="$3" actual
  actual="$(curl -s -o /dev/null -w '%{http_code}' -m 5 "${url}")"
  if [[ "${actual}" == "${expected}" ]]; then
    echo "ok   - ${description}"
  else
    echo "FAIL - ${description}: esperado ${expected}, obtido ${actual}"
    FAILURES=$((FAILURES + 1))
  fi
}

# expect_header: verifica se a resposta da URL contém o cabeçalho informado.
# Entrada: $1 descrição, $2 regex do cabeçalho, $3 URL. Saída: incrementa FAILURES se ausente.
expect_header() {
  local description="$1" pattern="$2" url="$3"
  if curl -s -D - -o /dev/null -m 5 "${url}" | grep -qiE "${pattern}"; then
    echo "ok   - ${description}"
  else
    echo "FAIL - ${description}: cabeçalho '${pattern}' ausente"
    FAILURES=$((FAILURES + 1))
  fi
}

mkdir -p "${WORK_DIR}/kb-assets/demo/.demo.tmp"
printf '\x89PNG\r\n\x1a\n' > "${WORK_DIR}/kb-assets/demo/a.png"
printf 'segredo' > "${WORK_DIR}/kb-assets/demo/.demo.tmp/b.png"
chmod -R a+rX "${WORK_DIR}"

docker run -d --name "${CONTAINER_NAME}" -p 127.0.0.1::80 \
  -v "${CONF_FILE}:/etc/nginx/conf.d/default.conf:ro" \
  -v "${WORK_DIR}/kb-assets:/usr/share/nginx/html/kb-assets:ro" \
  nginx:alpine >/dev/null
docker exec "${CONTAINER_NAME}" nginx -t
PORT="$(docker port "${CONTAINER_NAME}" 80/tcp | head -1 | cut -d: -f2)"
BASE="http://127.0.0.1:${PORT}"
for _ in $(seq 1 20); do curl -s -o /dev/null "${BASE}/" && break; sleep 0.25; done

expect_status "imagem existente responde 200" 200 "${BASE}/kb-assets/demo/a.png"
expect_header "content-type de PNG" '^content-type: image/png' "${BASE}/kb-assets/demo/a.png"
expect_header "cache de 1 dia" '^cache-control: .*max-age=86400' "${BASE}/kb-assets/demo/a.png"
expect_header "CSP sandbox (svg sem script)" '^content-security-policy: sandbox' "${BASE}/kb-assets/demo/a.png"
expect_status "arquivo inexistente responde 404" 404 "${BASE}/kb-assets/demo/zzz.png"
expect_status "listagem do prefixo bloqueada" 404 "${BASE}/kb-assets/"
expect_status "listagem do slug bloqueada" 404 "${BASE}/kb-assets/demo/"
expect_status "diretório oculto bloqueado" 404 "${BASE}/kb-assets/demo/.demo.tmp/b.png"
expect_status "caminho fora do prefixo responde 404" 404 "${BASE}/index.html"

if (( FAILURES > 0 )); then
  echo "${FAILURES} teste(s) falharam"
  exit 1
fi
echo "Todos os testes passaram"
