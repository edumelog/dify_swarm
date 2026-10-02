#!/usr/bin/env bash
# Relay para um modelo de linguagem que roda no host (ex.: llama.cpp no Windows, ouvindo só em
# 127.0.0.1). Os containers do Dify não alcançam o loopback do host; este script mantém um container
# socat, na rede do host, que escuta num IP alcançável pelos containers e repassa para o modelo.
# Funciona com Docker Swarm ou Compose: por padrão escuta no gateway da rede "bridge", que existe
# em qualquer Docker. É para uso sob demanda: o container não reinicia sozinho.
#
# Uso: docker/llm-relay.sh up | down | status
# Variáveis: LLM_RELAY_NAME (padrão: bonsai-relay), LLM_RELAY_PORT (padrão: 18080),
# LLM_RELAY_BIND_IP (padrão: gateway da rede bridge), LLM_RELAY_TARGET (padrão: 127.0.0.1:8080),
# LLM_RELAY_IMAGE (padrão: alpine/socat).
set -euo pipefail

RELAY_NAME="${LLM_RELAY_NAME:-bonsai-relay}"
RELAY_PORT="${LLM_RELAY_PORT:-18080}"
RELAY_TARGET="${LLM_RELAY_TARGET:-127.0.0.1:8080}"
RELAY_IMAGE="${LLM_RELAY_IMAGE:-alpine/socat}"
HEALTH_TIMEOUT_SECONDS=3

# fail: escreve uma mensagem de erro em stderr e encerra com código 1.
# Entrada: $* mensagem. Saída: nenhuma (encerra o script).
fail() {
  echo "ERRO: $*" >&2
  exit 1
}

# usage: mostra a forma de uso em stderr.
# Entrada: nenhuma. Saída: texto de ajuda em stderr.
usage() {
  echo "Uso: $0 up | down | status   (container: ${RELAY_NAME})" >&2
}

# bind_ip: define o IP em que o relay escuta.
# Entrada: nenhuma. Saída: LLM_RELAY_BIND_IP ou o gateway da rede bridge em stdout; encerra com erro se não achar.
bind_ip() {
  if [[ -n "${LLM_RELAY_BIND_IP:-}" ]]; then
    echo "${LLM_RELAY_BIND_IP}"
    return 0
  fi
  local gateway
  gateway="$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true)"
  [[ -n "${gateway}" ]] || fail "não foi possível descobrir o gateway da rede bridge; defina LLM_RELAY_BIND_IP."
  echo "${gateway}"
}

# socat_command: monta o comando do socat do relay.
# Entrada: $1 IP de escuta. Saída: argumentos do socat em stdout (separados por espaço).
socat_command() {
  echo "TCP-LISTEN:${RELAY_PORT},bind=$1,fork,reuseaddr TCP:${RELAY_TARGET}"
}

# relay_state: informa o estado do container.
# Entrada: nenhuma. Saída: "absent", "stopped" ou "running" em stdout.
relay_state() {
  local running
  running="$(docker inspect -f '{{.State.Running}}' "${RELAY_NAME}" 2>/dev/null)" || { echo "absent"; return 0; }
  [[ "${running}" == "true" ]] && echo "running" || echo "stopped"
}

# relay_command: lê o comando com que o container foi criado.
# Entrada: nenhuma. Saída: comando em stdout (vazio se não houver container).
relay_command() {
  docker inspect -f '{{join .Config.Cmd " "}}' "${RELAY_NAME}" 2>/dev/null || true
}

# model_responds: confere se o modelo responde no destino do relay.
# Entrada: nenhuma. Saída: código 0 se /v1/models responder.
model_responds() {
  curl -s -m "${HEALTH_TIMEOUT_SECONDS}" -o /dev/null "http://${RELAY_TARGET}/v1/models"
}

# show_dify_url: mostra a URL a usar no Dify.
# Entrada: $1 IP de escuta. Saída: texto em stderr.
show_dify_url() {
  echo "URL para o Dify (API Base URL): http://$1:${RELAY_PORT}/v1" >&2
}

# create_relay: cria e inicia o container do relay, sem reinício automático.
# Entrada: $1 IP de escuta. Saída: container no ar; encerra com erro se o docker falhar.
create_relay() {
  local args
  read -r -a args <<<"$(socat_command "$1")"
  docker run -d --name "${RELAY_NAME}" --restart no --network host "${RELAY_IMAGE}" "${args[@]}" >/dev/null \
    || fail "não foi possível criar o container ${RELAY_NAME} (a porta ${RELAY_PORT} já está em uso?)."
}

# relay_up: sobe o relay, recriando o container se a configuração mudou.
# Entrada: nenhuma. Saída: relay no ar, URL do Dify e aviso se o modelo não responder.
relay_up() {
  local ip wanted state
  ip="$(bind_ip)"
  wanted="$(socat_command "${ip}")"
  state="$(relay_state)"
  if [[ "${state}" != "absent" && "$(relay_command)" != "${wanted}" ]]; then
    echo "O container ${RELAY_NAME} existe com uma configuração diferente ($(relay_command)); recriando." >&2
    docker rm -f "${RELAY_NAME}" >/dev/null
    state="absent"
  fi
  case "${state}" in
    absent) create_relay "${ip}"; echo "Relay ${RELAY_NAME} criado e no ar: ${ip}:${RELAY_PORT} → ${RELAY_TARGET}." >&2 ;;
    stopped) docker start "${RELAY_NAME}" >/dev/null; echo "Relay ${RELAY_NAME} iniciado: ${ip}:${RELAY_PORT} → ${RELAY_TARGET}." >&2 ;;
    running) echo "Relay ${RELAY_NAME} já está no ar: ${ip}:${RELAY_PORT} → ${RELAY_TARGET}." >&2 ;;
  esac
  show_dify_url "${ip}"
  model_responds \
    || echo "AVISO: o modelo não responde em http://${RELAY_TARGET}/v1/models; inicie o modelo no host." >&2
}

# relay_down: para o relay (o container é mantido para o próximo 'up').
# Entrada: nenhuma. Saída: relay parado.
relay_down() {
  case "$(relay_state)" in
    running) docker stop "${RELAY_NAME}" >/dev/null; echo "Relay ${RELAY_NAME} parado." >&2 ;;
    stopped) echo "Relay ${RELAY_NAME} já está parado." >&2 ;;
    absent) echo "O container ${RELAY_NAME} não existe; nada a parar." >&2 ;;
  esac
}

# command_bind_ip: extrai o IP de escuta de um comando do socat.
# Entrada: $1 comando (ex.: "TCP-LISTEN:18080,bind=172.18.0.1,..."). Saída: IP em stdout (vazio se não houver).
command_bind_ip() {
  sed -n 's/.*bind=\([^,]*\),.*/\1/p' <<<"$1"
}

# relay_status: mostra o estado do relay, a URL do Dify e se o modelo responde.
# Entrada: nenhuma. Saída: texto em stderr.
relay_status() {
  local ip state current wanted
  ip="$(bind_ip)"
  wanted="$(socat_command "${ip}")"
  state="$(relay_state)"
  current="$(relay_command)"
  case "${state}" in
    running) echo "Relay ${RELAY_NAME}: no ar (${current})." >&2 ;;
    stopped) echo "Relay ${RELAY_NAME}: parado (${current}). Suba com: $0 up" >&2 ;;
    absent) echo "Relay ${RELAY_NAME}: o container não existe. Crie com: $0 up" >&2 ;;
  esac
  if [[ "${state}" != "absent" && "${current}" != "${wanted}" ]]; then
    [[ -z "$(command_bind_ip "${current}")" ]] || ip="$(command_bind_ip "${current}")"
    echo "AVISO: o container usa uma configuração diferente da padrão; '$0 up' o recria com: ${wanted}" >&2
  fi
  show_dify_url "${ip}"
  if model_responds; then
    echo "Modelo: o modelo responde em http://${RELAY_TARGET}/v1/models." >&2
  else
    echo "Modelo: não responde em http://${RELAY_TARGET}/v1/models (está iniciado no host?)." >&2
  fi
}

# main: despacha para up, down ou status.
# Entrada: $1 comando. Saída: código 0 em sucesso; 1 com uso inválido.
main() {
  (( $# == 1 )) || { usage; exit 1; }
  case "$1" in
    up) relay_up ;;
    down) relay_down ;;
    status) relay_status ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"
