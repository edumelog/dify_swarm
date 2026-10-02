#!/usr/bin/env bash
# Testa o docker-compose.kb.yaml (kb_assets e kb_admin no modo Docker Compose) sem subir nada:
# valida o compose combinado, confere que o template do nginx só difere do original do Dify pelas
# rotas do kb e roda 'nginx -t' na configuração gerada.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_TEMPLATE="${SCRIPT_DIR}/nginx/compose/default.conf.template"
DIFY_TEMPLATE="${SCRIPT_DIR}/nginx/conf.d/default.conf.template"
KB_BLOCK_START="# >>> kb_assets e kb_admin"
KB_BLOCK_END="# <<< kb_assets e kb_admin"
FAILURES=0

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

# compose_config: imprime o compose combinado (Dify + kb), usando o .env.example para a interpolação.
# Entrada: nenhuma. Saída: YAML normalizado em stdout.
compose_config() {
  docker compose --env-file "${SCRIPT_DIR}/.env.example" \
    -f "${SCRIPT_DIR}/docker-compose.yaml" -f "${SCRIPT_DIR}/docker-compose.kb.yaml" config 2>/dev/null
}

# without_kb_block: remove do template do Compose o bloco das rotas do kb.
# Entrada: nenhuma. Saída: template sem o bloco em stdout.
without_kb_block() {
  sed "/${KB_BLOCK_START}/,/${KB_BLOCK_END}/d" "${COMPOSE_TEMPLATE}" | grep -v '^# Versão para Docker Compose'
}

# nginx_test: gera a configuração do nginx com o entrypoint do Dify e roda 'nginx -t'.
# Entrada: nenhuma. Saída: código 0 se a configuração for válida.
nginx_test() {
  [[ -f "${COMPOSE_TEMPLATE}" ]] || return 1
  docker run --rm \
    --add-host api:127.0.0.1 --add-host web:127.0.0.1 --add-host plugin_daemon:127.0.0.1 \
    --add-host kb_assets:127.0.0.1 --add-host kb_admin:127.0.0.1 \
    -e NGINX_PORT=80 -e NGINX_SERVER_NAME=_ -e NGINX_HTTPS_ENABLED=false -e NGINX_WORKER_PROCESSES=auto \
    -e NGINX_CLIENT_MAX_BODY_SIZE=100M -e NGINX_KEEPALIVE_TIMEOUT=65 \
    -e NGINX_PROXY_READ_TIMEOUT=3600s -e NGINX_PROXY_SEND_TIMEOUT=3600s \
    -v "${SCRIPT_DIR}/nginx/nginx.conf.template:/etc/nginx/nginx.conf.template:ro" \
    -v "${SCRIPT_DIR}/nginx/proxy.conf.template:/etc/nginx/proxy.conf.template:ro" \
    -v "${SCRIPT_DIR}/nginx/https.conf.template:/etc/nginx/https.conf.template:ro" \
    -v "${COMPOSE_TEMPLATE}:/etc/nginx/conf.d/default.conf.template:ro" \
    -v "${SCRIPT_DIR}/nginx/docker-entrypoint.sh:/docker-entrypoint-mount.sh:ro" \
    nginx:latest sh -c "sed 's/^exec nginx .*/nginx -t/' /docker-entrypoint-mount.sh > /tmp/entrypoint.sh && bash /tmp/entrypoint.sh" >/dev/null 2>&1
}

CONFIG="$(compose_config)"
check "compose combinado é válido" "$([[ -n "${CONFIG}" ]]; echo $?)"
check "tem o serviço kb_assets" "$(grep -q '^  kb_assets:' <<<"${CONFIG}"; echo $?)"
check "tem o serviço kb_admin" "$(grep -q '^  kb_admin:' <<<"${CONFIG}"; echo $?)"
check "kb_admin fala com a api pelo nome do Compose" "$(grep -q 'DIFY_API_URL: http://api:5001' <<<"${CONFIG}"; echo $?)"
check "nginx usa o template com as rotas do kb" "$(grep -q 'nginx/compose/default.conf.template' <<<"${CONFIG}"; echo $?)"
check "template do Compose tem /kb-assets/ e /kb-admin" \
  "$(grep -q 'location /kb-assets/' "${COMPOSE_TEMPLATE}" && grep -q 'location /kb-admin {' "${COMPOSE_TEMPLATE}"; echo $?)"
check "template do Compose = template do Dify + rotas do kb (atualize-o se o do Dify mudar)" \
  "$(diff -q <(without_kb_block) "${DIFY_TEMPLATE}" >/dev/null; echo $?)"
check "nginx -t aceita a configuração gerada" "$(nginx_test; echo $?)"

echo
if (( FAILURES > 0 )); then
  echo "${FAILURES} asserção(ões) falharam."
  exit 1
fi
echo "Todos os testes passaram."
