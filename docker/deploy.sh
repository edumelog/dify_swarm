#!/usr/bin/env bash
# Confere o .env e sobe o Dify com Docker Swarm (docker-stack.yml) ou com Docker Compose
# (docker-compose.yaml), conforme a resposta do usuário.
#
# Segredos vazios ou com o valor público do .env.example podem ser gerados ou digitados, e URLs
# públicas vazias são pedidas no terminal; o .env original é copiado para .env.bak.<data-hora>.
#
# Uso: docker/deploy.sh [--swarm | --compose]
# Variáveis: STACK_NAME (padrão: dify), ENV_FILE (padrão: docker/.env),
# ENV_EXAMPLE (padrão: docker/.env.example). Nenhum IP ou domínio de ambiente é fixo aqui:
# no Swarm, DIFY_PUBLIC_HOST e DIFY_PUBLIC_HOST_IP são pedidos se faltarem no .env, e a rede do
# NGPM vem de DIFY_PROXY_NETWORK (padrão: net_nginx_pm).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_NAME="${STACK_NAME:-dify}"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"
ENV_EXAMPLE="${ENV_EXAMPLE:-${SCRIPT_DIR}/.env.example}"
STACK_FILE="docker-stack.yml"
COMPOSE_FILE="docker-compose.yaml"
DEFAULT_PROXY_NETWORK="net_nginx_pm"
ENV_LINE_PATTERN='^[A-Za-z_][A-Za-z0-9_]*='
URL_PATTERN='^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$'
HOSTNAME_PATTERN='^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$'
IPV4_PATTERN='^((25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])$'
SECRET_LENGTH=42

REQUIRED_VARS=(
  SECRET_KEY DB_PASSWORD REDIS_PASSWORD PLUGIN_DAEMON_KEY PLUGIN_DIFY_INNER_API_KEY
  SANDBOX_API_KEY WEAVIATE_API_KEY WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS
  CONSOLE_API_URL CONSOLE_WEB_URL APP_API_URL APP_WEB_URL FILES_URL
)
SECRET_VARS=(
  SECRET_KEY DB_PASSWORD REDIS_PASSWORD PLUGIN_DAEMON_KEY PLUGIN_DIFY_INNER_API_KEY
  SANDBOX_API_KEY WEAVIATE_API_KEY WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS
)
# Exigidas só no Swarm: entrada de hosts do container web (extra_hosts) para o SSR alcançar o NGPM.
SWARM_REQUIRED_VARS=(DIFY_PUBLIC_HOST DIFY_PUBLIC_HOST_IP)
REQUIRED_URL_VARS=(CONSOLE_API_URL CONSOLE_WEB_URL APP_API_URL APP_WEB_URL FILES_URL)
PUBLIC_URL_VARS=("${REQUIRED_URL_VARS[@]}" SERVICE_API_URL)
# As duas chaves do Weaviate precisam ser iguais: a segunda sempre recebe o valor da primeira.
WEAVIATE_KEY="WEAVIATE_API_KEY"
WEAVIATE_ALLOWED_KEYS="WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS"

declare -A ENV_VALUES=()
declare -A EXAMPLE_VALUES=()
declare -A UPDATES=()
UPDATE_ORDER=()

# fail: escreve uma mensagem de erro em stderr e encerra com código 1.
# Entrada: $* mensagem. Saída: nenhuma (encerra o script).
fail() {
  echo "ERRO: $*" >&2
  exit 1
}

# warn: escreve um aviso em stderr sem interromper o script.
# Entrada: $* mensagem. Saída: nenhuma.
warn() {
  echo "AVISO: $*" >&2
}

# usage: mostra a forma de uso em stderr.
# Entrada: nenhuma. Saída: texto de ajuda em stderr.
usage() {
  echo "Uso: $0 [--swarm | --compose]" >&2
}

# load_env_file: lê um arquivo KEY=valor literalmente (sem executá-lo) para um array associativo.
# Entrada: $1 arquivo, $2 nome do array associativo de destino. Saída: array preenchido.
load_env_file() {
  local -n target="$2"
  local line
  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ "${line}" =~ ${ENV_LINE_PATTERN} ]] && target["${line%%=*}"]="${line#*=}"
  done < "$1"
}

# url_origin: extrai protocolo + host (+ porta) de uma URL.
# Entrada: $1 URL. Saída: origem em stdout (vazia se a URL não for http/https).
url_origin() {
  [[ "$1" =~ ^(https?://[^/]+) ]] && echo "${BASH_REMATCH[1]}" || true
}

# check_env: confere variáveis obrigatórias, segredos públicos, chaves do Weaviate e origens das URLs.
# Entrada: $1 modo (swarm|compose); ENV_VALUES e EXAMPLE_VALUES carregados. Saída: avisos em stderr; encerra com erro listando os problemas.
check_env() {
  local errors=() var origin first_origin="" required=("${REQUIRED_VARS[@]}")
  [[ "$1" != "swarm" ]] || required+=("${SWARM_REQUIRED_VARS[@]}")
  for var in "${required[@]}"; do
    [[ -n "${ENV_VALUES[${var}]:-}" ]] || errors+=("${var} está vazia ou ausente.")
  done
  for var in "${SECRET_VARS[@]}"; do
    if [[ -n "${ENV_VALUES[${var}]:-}" && "${ENV_VALUES[${var}]}" == "${EXAMPLE_VALUES[${var}]:-}" ]]; then
      errors+=("${var} ainda tem o valor público do .env.example; gere outro com 'openssl rand -base64 42'.")
    fi
  done
  if [[ "${ENV_VALUES[WEAVIATE_API_KEY]:-}" != "${ENV_VALUES[WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS]:-}" ]]; then
    errors+=("WEAVIATE_API_KEY e WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS precisam ter o mesmo valor.")
  fi
  if (( ${#errors[@]} > 0 )); then
    echo "ERRO: o arquivo ${ENV_FILE} precisa de ajustes (veja a tabela da seção 2 do README.swarm.md):" >&2
    printf '  - %s\n' "${errors[@]}" >&2
    exit 1
  fi
  for var in "${PUBLIC_URL_VARS[@]}"; do
    [[ -n "${ENV_VALUES[${var}]:-}" ]] || continue
    origin="$(url_origin "${ENV_VALUES[${var}]}")"
    first_origin="${first_origin:-${origin}}"
    [[ "${origin}" == "${first_origin}" ]] \
      || warn "${var} (${ENV_VALUES[${var}]}) tem origem diferente de ${first_origin}; use o mesmo protocolo e domínio em todas as URLs públicas."
  done
}

# ask_yes_no: faz uma pergunta s/N no terminal.
# Entrada: $1 pergunta. Saída: código 0 se a resposta for sim; 1 caso contrário (inclusive sem resposta).
ask_yes_no() {
  local answer=""
  printf '%s [s/N] ' "$1" >&2
  read -r answer || true
  [[ "${answer,,}" =~ ^(s|sim|y|yes)$ ]]
}

# secret_problem: descreve por que um segredo precisa ser preenchido.
# Entrada: $1 nome da variável. Saída: descrição em stdout (vazia se o valor for aceitável).
secret_problem() {
  local value="${ENV_VALUES[$1]:-}"
  if [[ -z "${value}" ]]; then
    echo "está vazia"
  elif [[ "${value}" == "${EXAMPLE_VALUES[$1]:-}" ]]; then
    echo "tem o valor público do .env.example"
  fi
}

# generate_secret: gera um segredo alfanumérico aleatório (seguro em URLs e DSNs).
# Entrada: nenhuma. Saída: segredo de SECRET_LENGTH caracteres em stdout.
generate_secret() {
  local secret=""
  while (( ${#secret} < SECRET_LENGTH )); do
    if command -v openssl >/dev/null 2>&1; then
      secret+="$(openssl rand -base64 64 | tr -dc 'A-Za-z0-9')"
    else
      secret+="$(head -c 64 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
    fi
  done
  echo "${secret:0:${SECRET_LENGTH}}"
}

# set_update: agenda a gravação de uma chave no .env e atualiza o valor em memória.
# Entrada: $1 chave, $2 valor. Saída: UPDATES, UPDATE_ORDER e ENV_VALUES atualizados.
set_update() {
  [[ -n "${UPDATES[$1]+x}" ]] || UPDATE_ORDER+=("$1")
  UPDATES["$1"]="$2"
  ENV_VALUES["$1"]="$2"
}

# warn_existing_postgres: avisa que trocar DB_PASSWORD não altera a senha de um banco já criado.
# Entrada: nenhuma. Saída: aviso em stderr se o volume do Postgres (Swarm ou Compose) já existir.
warn_existing_postgres() {
  local volume="${STACK_NAME}_dify_postgres_data"
  if docker volume inspect "${volume}" >/dev/null 2>&1 \
    || [[ -n "$(ls -A volumes/db/data 2>/dev/null)" ]]; then
    warn "o banco já existe (volume ${volume} ou volumes/db/data) e guarda a senha da criação; com uma DB_PASSWORD nova, altere também a senha dentro do Postgres."
  fi
}

# fill_secrets: oferece gerar ou digitar os segredos vazios ou com valor público.
# Entrada: ENV_VALUES e EXAMPLE_VALUES carregados; respostas no stdin. Saída: UPDATES preenchido; encerra se cancelado.
fill_secrets() {
  local pending=() var problem choice="" value
  for var in "${SECRET_VARS[@]}"; do
    [[ "${var}" != "${WEAVIATE_ALLOWED_KEYS}" ]] || continue
    problem="$(secret_problem "${var}")"
    [[ "${var}" != "${WEAVIATE_KEY}" || -n "${problem}" ]] || problem="$(secret_problem "${WEAVIATE_ALLOWED_KEYS}")"
    [[ -z "${problem}" ]] || { pending+=("${var}"); echo "  - ${var} ${problem}." >&2; }
  done
  (( ${#pending[@]} > 0 )) || return 0
  echo "Os segredos acima precisam de um valor próprio." >&2

  while [[ ! "${choice}" =~ ^[gdc]$ ]]; do
    printf '[g] gerar valores aleatórios  [d] digitar os valores  [c] cancelar: ' >&2
    read -r choice || fail "nenhuma opção escolhida; preencha os segredos no ${ENV_FILE} e rode o script de novo."
    choice="${choice,,}"
  done
  [[ "${choice}" != "c" ]] || fail "preenchimento cancelado; ajuste o ${ENV_FILE} conforme a seção 2 do README.swarm.md."
  [[ ! " ${pending[*]} " =~ " DB_PASSWORD " ]] || warn_existing_postgres

  for var in "${pending[@]}"; do
    value=""
    if [[ "${choice}" == "d" ]]; then
      printf '%s (Enter = gerar): ' "${var}" >&2
      read -r -s value || fail "entrada encerrada antes de preencher ${var}."
      echo >&2
    fi
    [[ -n "${value}" ]] || value="$(generate_secret)"
    set_update "${var}" "${value}"
    [[ "${var}" != "${WEAVIATE_KEY}" ]] || set_update "${WEAVIATE_ALLOWED_KEYS}" "${value}"
  done
}

# fill_urls: pede no terminal as URLs públicas obrigatórias que estão vazias.
# Entrada: ENV_VALUES carregado; respostas no stdin. Saída: UPDATES preenchido; encerra se a entrada acabar.
fill_urls() {
  local var value last=""
  # Sugestão inicial: a primeira URL pública já preenchida; depois, a última digitada.
  for var in "${REQUIRED_URL_VARS[@]}"; do
    [[ -z "${ENV_VALUES[${var}]:-}" ]] || { last="${ENV_VALUES[${var}]}"; break; }
  done
  for var in "${REQUIRED_URL_VARS[@]}"; do
    [[ -z "${ENV_VALUES[${var}]:-}" ]] || continue
    while true; do
      if [[ -n "${last}" ]]; then
        printf '%s [%s]: ' "${var}" "${last}" >&2
      else
        printf '%s (ex.: https://dify.hmg.dti): ' "${var}" >&2
      fi
      read -r value || fail "entrada encerrada antes de preencher ${var}."
      value="${value%/}"
      value="${value:-${last}}"
      [[ "${value}" =~ ${URL_PATTERN} ]] && break
      echo "URL inválida: use http(s)://domínio[:porta], sem caminho." >&2
    done
    set_update "${var}" "${value}"
    last="${value}"
  done
}

# prompt_value: pede um valor no terminal até ele casar com um padrão; Enter aceita a sugestão.
# Entrada: $1 nome da variável, $2 sugestão (pode ser vazia), $3 regex, $4 mensagem de erro. Saída: valor em stdout.
prompt_value() {
  local value
  while true; do
    if [[ -n "$2" ]]; then printf '%s [%s]: ' "$1" "$2" >&2; else printf '%s: ' "$1" >&2; fi
    read -r value || fail "entrada encerrada antes de preencher $1."
    value="${value:-$2}"
    [[ "${value}" =~ $3 ]] && { echo "${value}"; return 0; }
    echo "$4" >&2
  done
}

# bridge_gateway: descobre o gateway da rede bridge do Docker, que costuma alcançar as portas publicadas no host.
# Entrada: nenhuma. Saída: IP em stdout (vazio se não encontrado).
bridge_gateway() {
  docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true
}

# fill_public_host: no Swarm, pede DIFY_PUBLIC_HOST e DIFY_PUBLIC_HOST_IP vazios, com sugestões do próprio ambiente.
# Entrada: ENV_VALUES carregado; respostas no stdin. Saída: UPDATES preenchido; aviso se o IP não aceitar conexão na 443.
fill_public_host() {
  local host suggestion value
  if [[ -z "${ENV_VALUES[DIFY_PUBLIC_HOST]:-}" ]]; then
    host="${ENV_VALUES[CONSOLE_API_URL]#*://}"
    suggestion="${host%%[:/]*}"
    echo "DIFY_PUBLIC_HOST: domínio público do Dify, resolvido dentro do container web." >&2
    value="$(prompt_value DIFY_PUBLIC_HOST "${suggestion}" "${HOSTNAME_PATTERN}" "Domínio inválido: informe só o nome, sem protocolo nem barras.")"
    set_update DIFY_PUBLIC_HOST "${value}"
  fi
  if [[ -z "${ENV_VALUES[DIFY_PUBLIC_HOST_IP]:-}" ]]; then
    echo "DIFY_PUBLIC_HOST_IP: IP pelo qual o container web alcança o NGPM na porta 443 (sugestão: gateway da rede bridge)." >&2
    value="$(prompt_value DIFY_PUBLIC_HOST_IP "$(bridge_gateway)" "${IPV4_PATTERN}" "IP inválido: use um IPv4, ex.: 10.100.2.25.")"
    set_update DIFY_PUBLIC_HOST_IP "${value}"
  fi
  timeout 3 bash -c "</dev/tcp/${ENV_VALUES[DIFY_PUBLIC_HOST_IP]}/443" 2>/dev/null \
    || warn "${ENV_VALUES[DIFY_PUBLIC_HOST_IP]}:443 não aceitou conexão a partir deste host; confira o IP (teste dentro do container na seção 8 do README.swarm.md)."
}

# write_env_updates: grava UPDATES no .env após copiar o original para .env.bak.<data-hora>.
# Entrada: UPDATES e UPDATE_ORDER preenchidos. Saída: .env atualizado preservando as demais linhas.
write_env_updates() {
  (( ${#UPDATE_ORDER[@]} > 0 )) || return 0
  local backup line key tmp
  local -A written=()
  backup="${ENV_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
  cp -p "${ENV_FILE}" "${backup}"
  tmp="$(mktemp)"
  while IFS= read -r line || [[ -n "${line}" ]]; do
    key="${line%%=*}"
    if [[ "${line}" =~ ${ENV_LINE_PATTERN} && -n "${UPDATES[${key}]+x}" ]]; then
      line="${key}=${UPDATES[${key}]}"
      written["${key}"]=1
    fi
    printf '%s\n' "${line}"
  done < "${ENV_FILE}" > "${tmp}"
  for key in "${UPDATE_ORDER[@]}"; do
    [[ -n "${written[${key}]+x}" ]] || printf '%s=%s\n' "${key}" "${UPDATES[${key}]}" >> "${tmp}"
  done
  cat "${tmp}" > "${ENV_FILE}"
  rm -f "${tmp}"
  echo "Preenchidas no ${ENV_FILE}: ${UPDATE_ORDER[*]} (backup em ${backup})." >&2
}

# ask_swarm_mode: pergunta se a subida é com Docker Swarm, sem resposta padrão.
# Entrada: respostas no stdin (só s/S/n/N; qualquer outra repete a pergunta). Saída: "swarm" ou "compose" em stdout; encerra se a entrada acabar.
ask_swarm_mode() {
  local answer
  while true; do
    printf 'Subir com Docker Swarm? [s/n] ' >&2
    read -r answer || fail "nenhuma resposta para o modo de subida; rode de novo ou use --swarm/--compose."
    case "${answer}" in
      s|S) echo "swarm"; return 0 ;;
      n|N) echo "compose"; return 0 ;;
      *) echo "Responda s ou n." >&2 ;;
    esac
  done
}

# export_env: exporta para o ambiente cada KEY=valor do .env, sem executar o arquivo.
# Entrada: ENV_VALUES carregado. Saída: variáveis exportadas no processo atual.
export_env() {
  local key
  for key in "${!ENV_VALUES[@]}"; do
    export "${key}=${ENV_VALUES[${key}]}"
  done
}

# deploy_swarm: confere os pré-requisitos do Swarm, valida o manifesto e faz o deploy após confirmação.
# Entrada: ENV_VALUES carregado. Saída: stack implantada, ou encerra com erro/cancelamento.
deploy_swarm() {
  local ca_file="${ENV_VALUES[DIFY_EXTRA_CA_FILE]:-./certs/no-extra-ca.pem}"
  local proxy_network="${ENV_VALUES[DIFY_PROXY_NETWORK]:-${DEFAULT_PROXY_NETWORK}}"
  [[ "$(docker info --format '{{.Swarm.ControlAvailable}}' 2>/dev/null)" == "true" ]] \
    || fail "este Docker não é manager de um Swarm ativo (rode 'docker swarm init' ou use um nó manager)."
  docker network inspect "${proxy_network}" >/dev/null 2>&1 \
    || fail "a rede externa '${proxy_network}' (DIFY_PROXY_NETWORK) não existe; crie-a com 'docker network create -d overlay --attachable ${proxy_network}'."
  [[ -f "${ca_file}" ]] || fail "DIFY_EXTRA_CA_FILE aponta para '${ca_file}', que não existe."

  export_env
  docker stack config -c "${STACK_FILE}" >/dev/null || fail "o manifesto ${STACK_FILE} é inválido."
  echo "Manifesto ${STACK_FILE} válido." >&2
  echo "Stack: ${STACK_NAME} | Domínio: ${ENV_VALUES[CONSOLE_WEB_URL]}" >&2
  if ! ask_yes_no "Fazer o deploy da stack '${STACK_NAME}' no Swarm?"; then
    echo "Deploy cancelado." >&2
    return 0
  fi
  docker stack deploy -c "${STACK_FILE}" "${STACK_NAME}"
  echo "Deploy enviado. Acompanhe com: docker stack services ${STACK_NAME}" >&2
}

# deploy_compose: valida o docker-compose.yaml e sobe os serviços após confirmação.
# Entrada: ENV_FILE conferido. Saída: serviços no ar, ou encerra com erro/cancelamento.
deploy_compose() {
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" config -q \
    || fail "o arquivo ${COMPOSE_FILE} é inválido."
  echo "Arquivo ${COMPOSE_FILE} válido." >&2
  echo "Domínio: ${ENV_VALUES[CONSOLE_WEB_URL]}" >&2
  if ! ask_yes_no "Subir o Dify com Docker Compose?"; then
    echo "Subida cancelada." >&2
    return 0
  fi
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d
  echo "Serviços no ar. Acompanhe com: docker compose -f ${COMPOSE_FILE} ps" >&2
}

# main: interpreta os argumentos, confere o .env e despacha para o modo escolhido.
# Entrada: $@ argumentos da linha de comando. Saída: código 0 em sucesso ou cancelamento.
main() {
  local mode=""
  case "${1:-}" in
    "") ;;
    --swarm) mode="swarm" ;;
    --compose) mode="compose" ;;
    *) usage; exit 1 ;;
  esac
  (( $# <= 1 )) || { usage; exit 1; }

  cd "${SCRIPT_DIR}"
  [[ -f "${ENV_FILE}" ]] || fail "arquivo ${ENV_FILE} não encontrado; crie-o com 'cp .env.example .env' e preencha conforme a seção 2 do README.swarm.md."
  [[ -f "${ENV_EXAMPLE}" ]] || fail "arquivo ${ENV_EXAMPLE} não encontrado; ele é usado para detectar segredos públicos."
  load_env_file "${ENV_FILE}" ENV_VALUES
  load_env_file "${ENV_EXAMPLE}" EXAMPLE_VALUES
  fill_secrets
  fill_urls
  if [[ -z "${mode}" ]]; then
    mode="$(ask_swarm_mode)" || exit 1
  fi
  [[ "${mode}" != "swarm" ]] || fill_public_host
  write_env_updates
  check_env "${mode}"
  echo "Variáveis do ${ENV_FILE} conferidas." >&2

  if [[ "${mode}" == "swarm" ]]; then deploy_swarm; else deploy_compose; fi
}

main "$@"
