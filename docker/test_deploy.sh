#!/usr/bin/env bash
# Testes do docker/deploy.sh com um `docker` simulado no PATH (nada sobe de verdade).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY="${SCRIPT_DIR}/deploy.sh"
WORK_DIR="$(mktemp -d)"
FAILURES=0

# cleanup: remove os arquivos temporários dos testes.
# Entrada: nenhuma. Saída: nenhuma.
cleanup() {
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

# Stub do docker: registra cada chamada em MOCK_LOG e responde conforme MOCK_MANAGER/MOCK_NETWORK/MOCK_PG_VOLUME;
# `network inspect bridge` devolve o gateway MOCK_BRIDGE_GW; `config inspect` devolve MOCK_CA_CONFIG (ou falha se vazio).
# Após o deploy: `service ls` devolve MOCK_REPLICAS; `exec` no Postgres só mostra a versão do banco depois de
# MOCK_DB_POLLS consultas (contador em MOCK_STATE_DIR); `exec` no Redis devolve MOCK_LOCK (1 = migração em andamento).
mkdir -p "${WORK_DIR}/bin"
cat > "${WORK_DIR}/bin/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "${MOCK_LOG}"
case "$1" in
  info) echo "${MOCK_MANAGER:-true}" ;;
  network)
    if [[ "$3" == "bridge" ]]; then echo "${MOCK_BRIDGE_GW:-172.30.0.1}"; else [[ "${MOCK_NETWORK:-1}" == "1" ]]; fi ;;
  volume) [[ "${MOCK_PG_VOLUME:-0}" == "1" ]] ;;
  config) [[ -n "${MOCK_CA_CONFIG:-}" ]] && printf '%s\n' "${MOCK_CA_CONFIG}" ;;
  service) printf '%b\n' "${MOCK_REPLICAS:-1/1\n1/1}" ;;
  ps) [[ "$*" == *db_postgres* ]] && echo "cid_db" || echo "cid_redis" ;;
  exec)
    if [[ "$2" == "cid_db" ]]; then
      n="$(cat "${MOCK_STATE_DIR}/db_polls" 2>/dev/null || echo "${MOCK_DB_POLLS:-0}")"
      if (( n > 0 )); then echo $((n - 1)) > "${MOCK_STATE_DIR}/db_polls"; exit 1; fi
      echo "c3f1a9b2e6d4"
    else
      echo "${MOCK_LOCK:-0}"
    fi ;;
  build) [[ "${MOCK_BUILD_FAIL:-0}" == "1" ]] && exit 1; echo "sha256:0123456789abcdef0123456789abcdef" ;;
  stack) [[ "$2" != "config" ]] || { echo "SECRET_KEY_SEEN=${SECRET_KEY:-}"; echo "KB_ADMIN_IMAGE_SEEN=${KB_ADMIN_IMAGE:-}"; echo "KB_ASSETS_CONF_HASH_SEEN=${KB_ASSETS_CONF_HASH:-}"; } >> "${MOCK_LOG}" ;;
esac
EOF
chmod +x "${WORK_DIR}/bin/docker"
export PATH="${WORK_DIR}/bin:${PATH}"

# Exemplo mínimo com os valores públicos que o script deve recusar.
cat > "${WORK_DIR}/env.example" <<'EOF'
SECRET_KEY=sk-public
DB_PASSWORD=difyai123456
REDIS_PASSWORD=difyai123456
PLUGIN_DAEMON_KEY=public-daemon
PLUGIN_DIFY_INNER_API_KEY=public-inner
SANDBOX_API_KEY=dify-sandbox
WEAVIATE_API_KEY=public-weaviate
WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS=public-weaviate
CONSOLE_API_URL=
EOF
# Pasta de certificados dos testes (CERTS_DIR): CAs públicas válidas, um arquivo com chave e um que não é certificado.
CERTS_DIR="${WORK_DIR}/certs"
mkdir -p "${CERTS_DIR}"
CA_CONTENT="$(printf '%s\n' '-----BEGIN CERTIFICATE-----' 'MIIBfakeca' '-----END CERTIFICATE-----')"
for name in ca.pem localCA.pem outra.pem; do printf '%s\n' "${CA_CONTENT}" > "${CERTS_DIR}/${name}"; done
printf '%s\n' "${CA_CONTENT}" '-----BEGIN PRIVATE KEY-----' 'MIIEsecret' '-----END PRIVATE KEY-----' > "${CERTS_DIR}/chave.pem"
printf 'texto qualquer\n' > "${CERTS_DIR}/lixo.pem"
export CERTS_DIR DEPLOY_POLL_SECONDS=0

# make_env: grava um .env válido em $1, aplicando as substituições KEY=valor passadas a seguir.
# Entrada: $1 caminho do .env, $2.. pares KEY=valor (valor vazio permitido). Saída: arquivo gravado.
make_env() {
  local file="$1" pair key
  shift
  cat > "${file}" <<EOF
# Comentário que deve sobreviver ao preenchimento
SECRET_KEY=real-secret
DB_PASSWORD=real-db
REDIS_PASSWORD=real-redis
PLUGIN_DAEMON_KEY=real-daemon
PLUGIN_DIFY_INNER_API_KEY=real-inner
SANDBOX_API_KEY=real-sandbox
WEAVIATE_API_KEY=real-weaviate
WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS=real-weaviate
CONSOLE_API_URL=https://dify.hmg.dti
CONSOLE_WEB_URL=https://dify.hmg.dti
APP_API_URL=https://dify.hmg.dti
APP_WEB_URL=https://dify.hmg.dti
FILES_URL=https://dify.hmg.dti
LOG_DATEFORMAT=%Y-%m-%d %H:%M:%S
DIFY_PUBLIC_HOST=dify.hmg.dti
DIFY_PUBLIC_HOST_IP=127.0.0.1
DIFY_EXTRA_CA_FILE=./certs/ca.pem
EOF
  for pair in "$@"; do
    key="${pair%%=*}"
    sed -i "/^${key}=/d" "${file}"
    echo "${pair}" >> "${file}"
  done
}

# run_deploy: executa o deploy.sh com o .env $1, a entrada $2 e os argumentos seguintes.
# Entrada: $1 .env, $2 texto do stdin, $3.. argumentos. Saída: stdout+stderr em OUT, código em RC, log em MOCK_LOG.
run_deploy() {
  local env_file="$1" input="$2"
  shift 2
  export MOCK_LOG="${WORK_DIR}/docker.log" MOCK_STATE_DIR="${WORK_DIR}/state"
  : > "${MOCK_LOG}"
  rm -rf "${MOCK_STATE_DIR}"; mkdir -p "${MOCK_STATE_DIR}"
  OUT="$(printf '%b' "${input}" | ENV_FILE="${env_file}" ENV_EXAMPLE="${WORK_DIR}/env.example" \
    bash "${DEPLOY}" "$@" 2>&1)"
  RC=$?
}

# test_missing_env: sem .env o script falha sem chamar o docker.
# Entrada: nenhuma. Saída: registra asserções via check.
test_missing_env() {
  run_deploy "${WORK_DIR}/nao-existe.env" "" --swarm
  check "falha sem .env" "$([[ ${RC} -ne 0 ]]; echo $?)"
  check "mensagem cita o .env ausente" "$(grep -q "não encontrado" <<<"${OUT}"; echo $?)"
  check "não chama o docker sem .env" "$([[ ! -s ${MOCK_LOG} ]]; echo $?)"
}

# test_empty_and_public_values: sem resposta à pergunta, lista os segredos pendentes e não altera nada.
# Entrada: nenhuma. Saída: registra asserções via check.
test_empty_and_public_values() {
  local env="${WORK_DIR}/bad.env" before
  make_env "${env}" "SECRET_KEY=" "DB_PASSWORD=difyai123456"
  before="$(cat "${env}")"
  run_deploy "${env}" "" --swarm
  check "falha sem resposta" "$([[ ${RC} -ne 0 ]]; echo $?)"
  check "aponta SECRET_KEY vazia" "$(grep -q "SECRET_KEY.*vazia" <<<"${OUT}"; echo $?)"
  check "aponta DB_PASSWORD igual ao exemplo" "$(grep -q "DB_PASSWORD.*\.env.example" <<<"${OUT}"; echo $?)"
  check "não sobe nada" "$(! grep -q "deploy\|up -d" "${MOCK_LOG}"; echo $?)"
  check "não altera o .env" "$([[ "$(cat "${env}")" == "${before}" ]]; echo $?)"
  check "não cria backup" "$(! ls "${env}".bak.* >/dev/null 2>&1; echo $?)"
}

# env_value: lê o valor de uma chave de um arquivo .env.
# Entrada: $1 arquivo, $2 chave. Saída: valor em stdout.
env_value() {
  grep -m1 "^$2=" "$1" | cut -d= -f2-
}

# test_generate_secrets: opção g gera segredos aleatórios, faz backup e preserva o resto do arquivo.
# Entrada: nenhuma. Saída: registra asserções via check.
test_generate_secrets() {
  local env="${WORK_DIR}/gen.env" before secret
  rm -f "${WORK_DIR}"/gen.env.bak.*
  make_env "${env}" "SECRET_KEY=" "DB_PASSWORD=difyai123456" \
    "WEAVIATE_API_KEY=public-weaviate" "WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS=public-weaviate"
  before="$(cat "${env}")"
  run_deploy "${env}" "g\n\nn\n" --swarm
  secret="$(env_value "${env}" SECRET_KEY)"
  check "segue até a confirmação após gerar" "$([[ ${RC} -eq 0 ]] && grep -q "docker stack config" "${MOCK_LOG}"; echo $?)"
  check "gera SECRET_KEY alfanumérica de 42 caracteres" "$([[ "${secret}" =~ ^[A-Za-z0-9]{42}$ ]]; echo $?)"
  check "troca DB_PASSWORD do exemplo" "$([[ "$(env_value "${env}" DB_PASSWORD)" =~ ^[A-Za-z0-9]{42}$ ]]; echo $?)"
  check "chaves do Weaviate iguais entre si e novas" "$([[ "$(env_value "${env}" WEAVIATE_API_KEY)" == "$(env_value "${env}" WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS)" && "$(env_value "${env}" WEAVIATE_API_KEY)" != "public-weaviate" ]]; echo $?)"
  check "mantém os segredos já válidos" "$([[ "$(env_value "${env}" REDIS_PASSWORD)" == "real-redis" ]]; echo $?)"
  check "preserva comentário e valores com espaço" "$(grep -q "^# Comentário que deve sobreviver" "${env}" && grep -q "^LOG_DATEFORMAT=%Y-%m-%d %H:%M:%S$" "${env}"; echo $?)"
  check "cria backup com o conteúdo original" "$([[ "$(cat "${env}".bak.* 2>/dev/null)" == "${before}" ]]; echo $?)"
  check "não exibe o valor gerado" "$(! grep -qF "${secret}" <<<"${OUT}"; echo $?)"
}

# test_type_secrets: opção d grava o valor digitado e gera quando a resposta é vazia.
# Entrada: nenhuma. Saída: registra asserções via check.
test_type_secrets() {
  local env="${WORK_DIR}/type.env"
  make_env "${env}" "SECRET_KEY=" "REDIS_PASSWORD=difyai123456"
  run_deploy "${env}" "d\nminha-chave\n\n\nn\n" --swarm
  check "grava o valor digitado" "$([[ "$(env_value "${env}" SECRET_KEY)" == "minha-chave" ]]; echo $?)"
  check "gera quando a resposta é vazia" "$([[ "$(env_value "${env}" REDIS_PASSWORD)" =~ ^[A-Za-z0-9]{42}$ ]]; echo $?)"
}

# test_cancel_secrets: opção c encerra sem alterar o .env.
# Entrada: nenhuma. Saída: registra asserções via check.
test_cancel_secrets() {
  local env="${WORK_DIR}/cancel.env" before
  make_env "${env}" "SECRET_KEY="
  before="$(cat "${env}")"
  run_deploy "${env}" "c\n" --swarm
  check "cancelar o preenchimento falha" "$([[ ${RC} -ne 0 ]]; echo $?)"
  check "cancelar não altera o .env" "$([[ "$(cat "${env}")" == "${before}" ]]; echo $?)"
}

# test_fill_urls: URLs vazias são pedidas; Enter repete a anterior e valores inválidos são recusados.
# Entrada: nenhuma. Saída: registra asserções via check.
test_fill_urls() {
  local env="${WORK_DIR}/urls.env"
  make_env "${env}" "CONSOLE_API_URL=" "FILES_URL="
  run_deploy "${env}" "ftp://x.dti\nhttps://x.dti/\n\n\nn\n" --swarm
  check "recusa URL inválida" "$(grep -q "inválida" <<<"${OUT}"; echo $?)"
  check "grava a URL digitada sem barra final" "$([[ "$(env_value "${env}" CONSOLE_API_URL)" == "https://x.dti" ]]; echo $?)"
  check "Enter repete a URL anterior" "$([[ "$(env_value "${env}" FILES_URL)" == "https://x.dti" ]]; echo $?)"
  check "mantém URLs já preenchidas" "$([[ "$(env_value "${env}" APP_WEB_URL)" == "https://dify.hmg.dti" ]]; echo $?)"
}

# test_postgres_volume_warning: avisa ao gerar DB_PASSWORD quando o volume do Postgres já existe.
# Entrada: nenhuma. Saída: registra asserções via check.
test_postgres_volume_warning() {
  local env="${WORK_DIR}/pg.env"
  make_env "${env}" "DB_PASSWORD=difyai123456"
  MOCK_PG_VOLUME=1 run_deploy "${env}" "g\n\nn\n" --swarm
  check "avisa sobre o volume do Postgres existente" "$(grep -q "AVISO.*dify_postgres_data" <<<"${OUT}"; echo $?)"
}

# test_weaviate_mismatch: as duas chaves do Weaviate precisam ser iguais.
# Entrada: nenhuma. Saída: registra asserções via check.
test_weaviate_mismatch() {
  local env="${WORK_DIR}/weaviate.env"
  make_env "${env}" "WEAVIATE_API_KEY=outra"
  run_deploy "${env}" "\n" --swarm
  check "falha com chaves do Weaviate diferentes" "$([[ ${RC} -ne 0 ]]; echo $?)"
  check "mensagem cita o Weaviate" "$(grep -q "WEAVIATE" <<<"${OUT}"; echo $?)"
}

# test_broker_follows_redis_password: o CELERY_BROKER_URL do Redis da stack recebe a REDIS_PASSWORD.
# Entrada: nenhuma. Saída: registra asserções via check.
test_broker_follows_redis_password() {
  local env="${WORK_DIR}/broker.env"
  rm -f "${WORK_DIR}"/broker.env.bak.*
  make_env "${env}" "CELERY_BROKER_URL=redis://:difyai123456@redis:6379/1"
  run_deploy "${env}" "\nn\n" --swarm
  check "broker usa a REDIS_PASSWORD" "$([[ "$(env_value "${env}" CELERY_BROKER_URL)" == "redis://:real-redis@redis:6379/1" ]]; echo $?)"
  check "avisa a troca do broker" "$(grep -q "CELERY_BROKER_URL" <<<"${OUT}"; echo $?)"
  check "não exibe a senha do Redis" "$(! grep -qF "real-redis" <<<"${OUT}"; echo $?)"
  check "faz backup ao trocar o broker" "$(ls "${env}".bak.* >/dev/null 2>&1; echo $?)"

  make_env "${env}" "REDIS_PASSWORD=difyai123456" "CELERY_BROKER_URL=redis://:difyai123456@redis:6379/1"
  run_deploy "${env}" "g\n\nn\n" --swarm
  check "broker recebe a REDIS_PASSWORD gerada" "$([[ "$(env_value "${env}" CELERY_BROKER_URL)" == "redis://:$(env_value "${env}" REDIS_PASSWORD)@redis:6379/1" ]]; echo $?)"
}

# test_external_broker_untouched: broker fora do Redis da stack, ou já alinhado, não é alterado.
# Entrada: nenhuma. Saída: registra asserções via check.
test_external_broker_untouched() {
  local env="${WORK_DIR}/ext-broker.env"
  rm -f "${WORK_DIR}"/ext-broker.env.bak.*
  make_env "${env}" "CELERY_BROKER_URL=redis://:outra@redis.externo:6379/1"
  run_deploy "${env}" "\nn\n" --swarm
  check "mantém broker externo" "$([[ "$(env_value "${env}" CELERY_BROKER_URL)" == "redis://:outra@redis.externo:6379/1" ]]; echo $?)"

  make_env "${env}" "CELERY_BROKER_URL=redis://:real-redis@redis:6379/1"
  run_deploy "${env}" "\nn\n" --swarm
  check "broker alinhado não gera backup" "$(! ls "${env}".bak.* >/dev/null 2>&1; echo $?)"
}

# test_mixed_origins_warns: URLs públicas com origens diferentes geram aviso, não erro.
# Entrada: nenhuma. Saída: registra asserções via check.
test_mixed_origins_warns() {
  local env="${WORK_DIR}/origins.env"
  make_env "${env}" "FILES_URL=http://outro.dti"
  run_deploy "${env}" "\nn\n" --swarm
  check "avisa sobre origens diferentes" "$(grep -q "AVISO.*origem" <<<"${OUT}"; echo $?)"
  check "segue até a confirmação" "$(grep -q "docker stack config" "${MOCK_LOG}"; echo $?)"
}

# test_swarm_deploy: modo Swarm valida com o .env exportado e faz o deploy após confirmar.
# Entrada: nenhuma. Saída: registra asserções via check.
test_swarm_deploy() {
  local env="${WORK_DIR}/ok.env"
  make_env "${env}"
  run_deploy "${env}" "s\n\ns\n"
  check "termina com sucesso" "$([[ ${RC} -eq 0 ]]; echo $?)"
  check "pergunta se é Swarm" "$(grep -q "Swarm" <<<"${OUT}"; echo $?)"
  check "exporta o .env antes do stack config" "$(grep -q "SECRET_KEY_SEEN=real-secret" "${MOCK_LOG}"; echo $?)"
  check "faz o stack deploy da stack dify" "$(grep -q "docker stack deploy -c docker-stack.yml dify" "${MOCK_LOG}"; echo $?)"
  check "constrói a imagem do kb_admin" "$(grep -q "docker build -q -t dify-kb-admin:local .*kb_admin" "${MOCK_LOG}"; echo $?)"
  check "marca a imagem com o ID" "$(grep -q "docker tag dify-kb-admin:local dify-kb-admin:0123456789ab" "${MOCK_LOG}"; echo $?)"
  check "exporta KB_ADMIN_IMAGE para a stack" "$(grep -q "KB_ADMIN_IMAGE_SEEN=dify-kb-admin:0123456789ab" "${MOCK_LOG}"; echo $?)"
  local conf_hash
  conf_hash="$(sha256sum "${SCRIPT_DIR}/kb_assets/default.conf" | cut -c1-12)"
  check "exporta o hash do default.conf do kb_assets" "$(grep -q "KB_ASSETS_CONF_HASH_SEEN=${conf_hash}" "${MOCK_LOG}"; echo $?)"
  check "não usa compose" "$(! grep -q "docker compose" "${MOCK_LOG}"; echo $?)"
}

# test_swarm_cancel: recusar a confirmação valida mas não faz deploy.
# Entrada: nenhuma. Saída: registra asserções via check.
test_swarm_cancel() {
  local env="${WORK_DIR}/ok.env"
  make_env "${env}"
  run_deploy "${env}" "\nn\n" --swarm
  check "cancelamento termina sem erro" "$([[ ${RC} -eq 0 ]]; echo $?)"
  check "valida o manifesto" "$(grep -q "docker stack config" "${MOCK_LOG}"; echo $?)"
  check "não faz deploy" "$(! grep -q "stack deploy" "${MOCK_LOG}"; echo $?)"
}

# test_swarm_prerequisites: Swarm exige nó manager, rede net_nginx_pm e arquivo da CA.
# Entrada: nenhuma. Saída: registra asserções via check.
test_swarm_prerequisites() {
  local env="${WORK_DIR}/ok.env"
  make_env "${env}"
  MOCK_MANAGER=false run_deploy "${env}" "\n" --swarm
  check "falha fora de um manager" "$([[ ${RC} -ne 0 ]] && grep -q "manager" <<<"${OUT}"; echo $?)"
  MOCK_NETWORK=0 run_deploy "${env}" "\n" --swarm
  check "falha sem a rede net_nginx_pm" "$([[ ${RC} -ne 0 ]] && grep -q "net_nginx_pm" <<<"${OUT}"; echo $?)"
  check "não faz deploy com pré-requisito faltando" "$(! grep -q "stack deploy" "${MOCK_LOG}"; echo $?)"
}

# test_fill_public_host_suggestions: no Swarm, Enter aceita o host de CONSOLE_API_URL e o gateway da rede bridge.
# Entrada: nenhuma. Saída: registra asserções via check.
test_fill_public_host_suggestions() {
  local env="${WORK_DIR}/host.env"
  make_env "${env}" "DIFY_PUBLIC_HOST=" "DIFY_PUBLIC_HOST_IP="
  run_deploy "${env}" "s\n\n\n\nn\n"
  check "sugere e grava o host de CONSOLE_API_URL" "$([[ "$(env_value "${env}" DIFY_PUBLIC_HOST)" == "dify.hmg.dti" ]]; echo $?)"
  check "sugere e grava o gateway da rede bridge" "$([[ "$(env_value "${env}" DIFY_PUBLIC_HOST_IP)" == "172.30.0.1" ]]; echo $?)"
  check "segue até a confirmação" "$([[ ${RC} -eq 0 ]] && grep -q "docker stack config" "${MOCK_LOG}"; echo $?)"
}

# test_fill_public_host_typed: valores digitados são gravados e IPs inválidos são recusados.
# Entrada: nenhuma. Saída: registra asserções via check.
test_fill_public_host_typed() {
  local env="${WORK_DIR}/host-typed.env"
  make_env "${env}" "DIFY_PUBLIC_HOST=" "DIFY_PUBLIC_HOST_IP="
  run_deploy "${env}" "chat.prd.dti\n999.1.1.1\n10.1.2.3\n\nn\n" --swarm
  check "grava o domínio digitado" "$([[ "$(env_value "${env}" DIFY_PUBLIC_HOST)" == "chat.prd.dti" ]]; echo $?)"
  check "recusa IP inválido" "$(grep -q "IP inválido" <<<"${OUT}"; echo $?)"
  check "grava o IP digitado" "$([[ "$(env_value "${env}" DIFY_PUBLIC_HOST_IP)" == "10.1.2.3" ]]; echo $?)"
}

# test_compose_skips_public_host: sem Swarm, DIFY_PUBLIC_HOST(_IP) não são pedidos nem exigidos.
# Entrada: nenhuma. Saída: registra asserções via check.
test_compose_skips_public_host() {
  local env="${WORK_DIR}/host-compose.env"
  make_env "${env}" "DIFY_PUBLIC_HOST=" "DIFY_PUBLIC_HOST_IP="
  run_deploy "${env}" "n\nn\n"
  check "compose não pede o domínio público" "$([[ ${RC} -eq 0 ]] && ! grep -q "DIFY_PUBLIC_HOST" <<<"${OUT}"; echo $?)"
}

# test_proxy_network_from_env: a rede do NGPM vem de DIFY_PROXY_NETWORK, com net_nginx_pm como padrão.
# Entrada: nenhuma. Saída: registra asserções via check.
test_proxy_network_from_env() {
  local env="${WORK_DIR}/net.env"
  make_env "${env}"
  run_deploy "${env}" "\nn\n" --swarm
  check "usa net_nginx_pm por padrão" "$(grep -q "docker network inspect net_nginx_pm" "${MOCK_LOG}"; echo $?)"
  make_env "${env}" "DIFY_PROXY_NETWORK=proxy_hmg"
  run_deploy "${env}" "\nn\n" --swarm
  check "usa DIFY_PROXY_NETWORK do .env" "$(grep -q "docker network inspect proxy_hmg" "${MOCK_LOG}"; echo $?)"
}

# test_stack_has_no_environment_defaults: o docker-stack.yml exige domínio e IP públicos do .env.
# Entrada: nenhuma. Saída: registra asserções via check.
test_stack_has_no_environment_defaults() {
  local stack="${SCRIPT_DIR}/docker-stack.yml"
  check "stack exige DIFY_PUBLIC_HOST" "$(grep -q 'DIFY_PUBLIC_HOST:?' "${stack}"; echo $?)"
  check "stack exige DIFY_PUBLIC_HOST_IP" "$(grep -q 'DIFY_PUBLIC_HOST_IP:?' "${stack}"; echo $?)"
  check "stack sem IP ou domínio de ambiente" "$(! grep -qE '\.dev\.dti|\.hmg\.dti|10\.0\.2\.2|172\.17\.' "${stack}"; echo $?)"
  check "stack exige KB_ADMIN_IMAGE" "$(grep -q 'KB_ADMIN_IMAGE:?' "${stack}"; echo $?)"
  check "config do kb_assets versionada pelo hash" "$(grep -q 'name: dify_kb_assets_conf_${KB_ASSETS_CONF_HASH:?' "${stack}"; echo $?)"
  check "kb_admin usa o nome completo da api" "$(grep -q 'com.docker.stack.namespace"}}_api:5001' "${stack}"; echo $?)"
}

# test_ca_default_suggestion: sem DIFY_EXTRA_CA_FILE, Enter aceita localCA.pem e grava o caminho no .env.
# Entrada: nenhuma. Saída: registra asserções via check.
test_ca_default_suggestion() {
  local env="${WORK_DIR}/ca-default.env"
  make_env "${env}" "DIFY_EXTRA_CA_FILE="
  run_deploy "${env}" "\nn\n" --swarm
  check "sugere localCA.pem" "$(grep -q "\[localCA.pem\]" <<<"${OUT}"; echo $?)"
  check "grava ./certs/localCA.pem" "$([[ "$(env_value "${env}" DIFY_EXTRA_CA_FILE)" == "./certs/localCA.pem" ]]; echo $?)"
}

# test_ca_keeps_current: com DIFY_EXTRA_CA_FILE preenchida, Enter mantém o arquivo atual sem regravar o .env.
# Entrada: nenhuma. Saída: registra asserções via check.
test_ca_keeps_current() {
  local env="${WORK_DIR}/ca-keep.env"
  rm -f "${env}".bak.*
  make_env "${env}"
  run_deploy "${env}" "\nn\n" --swarm
  check "sugere o arquivo atual" "$(grep -q "\[ca.pem\]" <<<"${OUT}"; echo $?)"
  check "não regrava o .env" "$(! ls "${env}".bak.* >/dev/null 2>&1; echo $?)"
}

# test_ca_custom_name: o operador pode trocar o nome por outro arquivo existente em certs/.
# Entrada: nenhuma. Saída: registra asserções via check.
test_ca_custom_name() {
  local env="${WORK_DIR}/ca-custom.env"
  make_env "${env}"
  run_deploy "${env}" "outra.pem\nn\n" --swarm
  check "grava o nome digitado" "$([[ "$(env_value "${env}" DIFY_EXTRA_CA_FILE)" == "./certs/outra.pem" ]]; echo $?)"
}

# test_ca_missing_alerts: arquivo ausente gera alerta e repete; sem resposta, não prossegue.
# Entrada: nenhuma. Saída: registra asserções via check.
test_ca_missing_alerts() {
  local env="${WORK_DIR}/ca-missing.env" alerts
  make_env "${env}"
  run_deploy "${env}" "faltando.pem\n\nca.pem\nn\n" --swarm
  alerts="$(grep -c "ALERTA.*faltando.pem" <<<"${OUT}")"
  check "alerta a cada tentativa com o arquivo ausente" "$([[ ${alerts} -eq 2 ]]; echo $?)"
  check "aceita quando o arquivo existe" "$([[ "$(env_value "${env}" DIFY_EXTRA_CA_FILE)" == "./certs/ca.pem" && ${RC} -eq 0 ]]; echo $?)"
  run_deploy "${env}" "faltando.pem\n" --swarm
  check "sem o arquivo não prossegue" "$([[ ${RC} -ne 0 ]] && ! grep -q "docker stack" "${MOCK_LOG}"; echo $?)"
}

# test_ca_none: "nenhum" grava DIFY_EXTRA_CA_FILE vazio (CA pública, sem CA extra).
# Entrada: nenhuma. Saída: registra asserções via check.
test_ca_none() {
  local env="${WORK_DIR}/ca-none.env"
  make_env "${env}"
  run_deploy "${env}" "nenhum\nn\n" --swarm
  check "nenhum grava valor vazio" "$(grep -qx "DIFY_EXTRA_CA_FILE=" "${env}"; echo $?)"
  check "nenhum segue até a confirmação" "$(grep -q "docker stack config" "${MOCK_LOG}"; echo $?)"
}

# test_ca_rejects_invalid_files: recusa arquivo com chave privada ou sem certificado.
# Entrada: nenhuma. Saída: registra asserções via check.
test_ca_rejects_invalid_files() {
  local env="${WORK_DIR}/ca-invalid.env"
  make_env "${env}"
  run_deploy "${env}" "chave.pem\nlixo.pem\nca.pem\nn\n" --swarm
  check "recusa arquivo com chave privada" "$(grep -q "chave.pem.*chave privada" <<<"${OUT}"; echo $?)"
  check "recusa arquivo sem certificado" "$(grep -q "lixo.pem.*BEGIN CERTIFICATE" <<<"${OUT}"; echo $?)"
  check "fica com o certificado válido" "$([[ "$(env_value "${env}" DIFY_EXTRA_CA_FILE)" == "./certs/ca.pem" ]]; echo $?)"
}

# test_ca_config_change_warning: avisa quando a CA escolhida difere do config da stack no ar.
# Entrada: nenhuma. Saída: registra asserções via check.
test_ca_config_change_warning() {
  local env="${WORK_DIR}/ca-change.env"
  make_env "${env}"
  MOCK_CA_CONFIG="outro conteúdo" run_deploy "${env}" "\nn\n" --swarm
  check "avisa sobre CA diferente da stack no ar" "$(grep -q "AVISO.*docker stack rm" <<<"${OUT}"; echo $?)"
  MOCK_CA_CONFIG="${CA_CONTENT}" run_deploy "${env}" "\nn\n" --swarm
  check "não avisa com a mesma CA" "$(! grep -q "docker stack rm" <<<"${OUT}"; echo $?)"
}

# test_certs_gitignore: só o localCA.pem de docker/certs/ é versionado; chaves continuam ignoradas.
# Entrada: nenhuma. Saída: registra asserções via check.
test_certs_gitignore() {
  check "localCA.pem é versionável" "$(! git -C "${SCRIPT_DIR}" check-ignore -q --no-index certs/localCA.pem; echo $?)"
  check "chaves em certs/ são ignoradas" "$(git -C "${SCRIPT_DIR}" check-ignore -q --no-index certs/localCA.key; echo $?)"
  check "outras CAs em certs/ são ignoradas" "$(git -C "${SCRIPT_DIR}" check-ignore -q --no-index certs/producao.pem; echo $?)"
  check "localCA.pem versionado não contém chave privada" "$(! grep -q "PRIVATE KEY" "${SCRIPT_DIR}/certs/localCA.pem"; echo $?)"
}

# test_waits_for_migration: após o deploy, espera o banco migrado antes de dizer que o Dify está pronto.
# Entrada: nenhuma. Saída: registra asserções via check.
test_waits_for_migration() {
  local env="${WORK_DIR}/wait.env"
  make_env "${env}"
  MOCK_DB_POLLS=2 run_deploy "${env}" "\ns\n" --swarm
  check "espera o banco ser migrado" "$([[ "$(grep -c "docker exec cid_db" "${MOCK_LOG}")" -ge 3 ]]; echo $?)"
  check "anuncia o Dify pronto com a versão do banco" "$([[ ${RC} -eq 0 ]] && grep -q "Dify pronto:.*c3f1a9b2e6d4" <<<"${OUT}"; echo $?)"
  check "consulta o banco do .env" "$(grep -q "psql -U postgres -d dify" "${MOCK_LOG}"; echo $?)"
}

# test_waits_for_lock_and_replicas: banco com versão não basta; espera a trava liberar e os serviços ficarem 1/1.
# Entrada: nenhuma. Saída: registra asserções via check.
test_waits_for_lock_and_replicas() {
  local env="${WORK_DIR}/wait-lock.env"
  make_env "${env}"
  MOCK_LOCK=1 DEPLOY_WAIT_TIMEOUT=0 run_deploy "${env}" "\ns\n" --swarm
  check "não anuncia pronto com migração em andamento" "$([[ ${RC} -ne 0 ]] && ! grep -q "Dify pronto:" <<<"${OUT}" && grep -q "migra" <<<"${OUT}"; echo $?)"
  MOCK_REPLICAS="1/1\n0/1" DEPLOY_WAIT_TIMEOUT=0 run_deploy "${env}" "\ns\n" --swarm
  check "não anuncia pronto com serviço fora do ar" "$([[ ${RC} -ne 0 ]] && ! grep -q "Dify pronto:" <<<"${OUT}"; echo $?)"
  MOCK_REPLICAS="1/1\n0/1 (1/1 completed)" run_deploy "${env}" "\ns\n" --swarm
  check "job concluído (init_permissions) conta como pronto" "$([[ ${RC} -eq 0 ]] && grep -q "Dify pronto:" <<<"${OUT}"; echo $?)"
}

# test_wait_timeout_message: sem banco migrado no prazo, falha com dica de diagnóstico.
# Entrada: nenhuma. Saída: registra asserções via check.
test_wait_timeout_message() {
  local env="${WORK_DIR}/wait-timeout.env"
  make_env "${env}"
  MOCK_DB_POLLS=99 DEPLOY_WAIT_TIMEOUT=0 run_deploy "${env}" "\ns\n" --swarm
  check "falha ao estourar o prazo" "$([[ ${RC} -ne 0 ]] && grep -q "docker service logs" <<<"${OUT}"; echo $?)"
}

# test_stack_postgres_start_period: o Postgres tem tempo para o initdb da primeira subida.
# Entrada: nenhuma. Saída: registra asserções via check.
test_stack_postgres_start_period() {
  check "db_postgres com start_period de 5m" "$(awk '/^  db_postgres:/{f=1} f&&/start_period/{print; exit}' "${SCRIPT_DIR}/docker-stack.yml" | grep -q "start_period: 5m"; echo $?)"
}

# test_env_example_ending_with_comment: .env.example terminado em comentário não derruba o script.
# Entrada: nenhuma. Saída: registra asserções via check.
test_env_example_ending_with_comment() {
  local env="${WORK_DIR}/ok.env" example="${WORK_DIR}/env.example.comment"
  make_env "${env}"
  { cat "${WORK_DIR}/env.example"; echo "# comentário final"; } > "${example}"
  MOCK_LOG="${WORK_DIR}/docker.log" OUT="$(printf '\nn\n' | ENV_FILE="${env}" ENV_EXAMPLE="${example}" \
    bash "${DEPLOY}" --swarm 2>&1)"
  check "aceita .env.example terminado em comentário" "$(grep -q "Variáveis do .* conferidas" <<<"${OUT}"; echo $?)"
}

# test_kb_admin_build_failure: falha no build do kb_admin interrompe o deploy com mensagem.
# Entrada: nenhuma. Saída: registra asserções via check.
test_kb_admin_build_failure() {
  local env="${WORK_DIR}/ok.env"
  make_env "${env}"
  MOCK_BUILD_FAIL=1 run_deploy "${env}" "\ns\n" --swarm
  check "falha se o build do kb_admin falhar" "$([[ ${RC} -ne 0 ]] && grep -q "kb_admin" <<<"${OUT}"; echo $?)"
  check "não faz deploy sem a imagem" "$(! grep -q "stack deploy" "${MOCK_LOG}"; echo $?)"
}

# test_compose_deploy: sem Swarm valida e sobe com docker compose.
# Entrada: nenhuma. Saída: registra asserções via check.
test_compose_deploy() {
  local env="${WORK_DIR}/ok.env"
  make_env "${env}"
  run_deploy "${env}" "n\ns\n"
  check "compose termina com sucesso" "$([[ ${RC} -eq 0 ]]; echo $?)"
  check "valida o compose" "$(grep -q "docker compose .*-f docker-compose.yaml config -q" "${MOCK_LOG}"; echo $?)"
  check "sobe com compose up -d" "$(grep -q "docker compose .*-f docker-compose.yaml up -d" "${MOCK_LOG}"; echo $?)"
  check "não usa stack" "$(! grep -q "docker stack" "${MOCK_LOG}"; echo $?)"
}

# test_mode_question_requires_answer: a pergunta do Swarm só aceita s/S/n/N e repete para qualquer outra resposta.
# Entrada: nenhuma. Saída: registra asserções via check.
test_mode_question_requires_answer() {
  local env="${WORK_DIR}/mode.env" asked
  make_env "${env}"
  run_deploy "${env}" "\nsim\nx\nS\n\nn\n"
  asked="$(grep -o "Subir com Docker Swarm?" <<<"${OUT}" | wc -l)"
  check "repete a pergunta até receber s/S/n/N" "$([[ ${asked} -eq 4 ]]; echo $?)"
  check "S maiúsculo escolhe o Swarm" "$(grep -q "docker stack config" "${MOCK_LOG}" && ! grep -q "docker compose" "${MOCK_LOG}"; echo $?)"
  run_deploy "${env}" "N\nn\n"
  check "N maiúsculo escolhe o Compose" "$(grep -q "docker compose" "${MOCK_LOG}" && ! grep -q "docker stack" "${MOCK_LOG}"; echo $?)"
  run_deploy "${env}" "\n"
  check "sem resposta não escolhe modo nem sobe nada" "$([[ ${RC} -ne 0 ]] && ! grep -q "docker stack\|docker compose" "${MOCK_LOG}"; echo $?)"
}

# test_invalid_argument: argumento desconhecido falha com a ajuda.
# Entrada: nenhuma. Saída: registra asserções via check.
test_invalid_argument() {
  run_deploy "${WORK_DIR}/ok.env" "" --xyz
  check "falha com argumento inválido" "$([[ ${RC} -ne 0 ]] && grep -q "Uso:" <<<"${OUT}"; echo $?)"
}

test_missing_env
test_empty_and_public_values
test_generate_secrets
test_type_secrets
test_cancel_secrets
test_fill_urls
test_postgres_volume_warning
test_weaviate_mismatch
test_broker_follows_redis_password
test_external_broker_untouched
test_mixed_origins_warns
test_swarm_deploy
test_swarm_cancel
test_swarm_prerequisites
test_fill_public_host_suggestions
test_fill_public_host_typed
test_compose_skips_public_host
test_proxy_network_from_env
test_stack_has_no_environment_defaults
test_ca_default_suggestion
test_ca_keeps_current
test_ca_custom_name
test_ca_missing_alerts
test_ca_none
test_ca_rejects_invalid_files
test_ca_config_change_warning
test_certs_gitignore
test_waits_for_migration
test_waits_for_lock_and_replicas
test_wait_timeout_message
test_stack_postgres_start_period
test_env_example_ending_with_comment
test_kb_admin_build_failure
test_compose_deploy
test_mode_question_requires_answer
test_invalid_argument

echo
if (( FAILURES > 0 )); then
  echo "${FAILURES} asserção(ões) falharam."
  exit 1
fi
echo "Todos os testes passaram."
