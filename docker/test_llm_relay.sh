#!/usr/bin/env bash
# Testes do docker/llm-relay.sh com `docker` e `curl` simulados no PATH (nada sobe de verdade).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELAY="${SCRIPT_DIR}/llm-relay.sh"
WORK_DIR="$(mktemp -d)"
FAILURES=0
EXPECTED_CMD="TCP-LISTEN:18080,bind=172.17.0.1,fork,reuseaddr TCP:127.0.0.1:8080"

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

# Stubs. Estado em MOCK_DIR: exists (container criado), running (no ar), cmd (comando do container).
# curl responde com sucesso só se MOCK_MODEL_UP=1.
mkdir -p "${WORK_DIR}/bin"
cat > "${WORK_DIR}/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "docker $*" >> "${MOCK_DIR}/log"
case "$1" in
  network) echo "172.17.0.1" ;;
  inspect)
    [[ -f "${MOCK_DIR}/exists" ]] || { echo "Error: No such object" >&2; exit 1; }
    case "$*" in
      *State.Running*) [[ -f "${MOCK_DIR}/running" ]] && echo true || echo false ;;
      *Config.Cmd*) cat "${MOCK_DIR}/cmd" ;;
    esac ;;
  run) touch "${MOCK_DIR}/exists" "${MOCK_DIR}/running"; shift; echo "${@: -2}" > "${MOCK_DIR}/cmd"; echo "cid" ;;
  start) touch "${MOCK_DIR}/running"; echo "$2" ;;
  stop) rm -f "${MOCK_DIR}/running"; echo "$2" ;;
  rm) rm -f "${MOCK_DIR}/exists" "${MOCK_DIR}/running" "${MOCK_DIR}/cmd"; echo "$3" ;;
esac
STUB
cat > "${WORK_DIR}/bin/curl" <<'STUB'
#!/usr/bin/env bash
echo "curl $*" >> "${MOCK_DIR}/log"
[[ "${MOCK_MODEL_UP:-1}" == "1" ]] && echo '{"data":[{"id":"modelo"}]}' || exit 7
STUB
chmod +x "${WORK_DIR}/bin/docker" "${WORK_DIR}/bin/curl"
export PATH="${WORK_DIR}/bin:${PATH}"

# setup_state: prepara o estado simulado do container.
# Entrada: $1 "absent", "stopped" ou "running"; $2 comando do container (opcional). Saída: MOCK_DIR preenchido.
setup_state() {
  export MOCK_DIR="${WORK_DIR}/state"
  rm -rf "${MOCK_DIR}"
  mkdir -p "${MOCK_DIR}"
  : > "${MOCK_DIR}/log"
  [[ "$1" == "absent" ]] && return 0
  touch "${MOCK_DIR}/exists"
  [[ "$1" != "running" ]] || touch "${MOCK_DIR}/running"
  echo "${2:-${EXPECTED_CMD}}" > "${MOCK_DIR}/cmd"
}

# run_relay: executa o llm-relay.sh com os argumentos dados.
# Entrada: $@ argumentos. Saída: stdout+stderr em OUT e código em RC.
run_relay() {
  OUT="$(bash "${RELAY}" "$@" 2>&1)"
  RC=$?
}

# logged: confere se um comando simulado foi chamado.
# Entrada: $1 trecho do comando. Saída: código 0 se encontrado.
logged() {
  grep -q -- "$1" "${MOCK_DIR}/log"
}

# test_up_creates: sem container, 'up' cria com o gateway da rede bridge e mostra a URL do Dify.
# Entrada: nenhuma. Saída: registra asserções via check.
test_up_creates() {
  setup_state absent
  run_relay up
  check "up cria o container" "$([[ ${RC} -eq 0 ]] && logged "docker run -d --name bonsai-relay --restart no --network host alpine/socat ${EXPECTED_CMD}"; echo $?)"
  check "up usa o gateway da rede bridge" "$(logged "docker network inspect bridge"; echo $?)"
  check "up mostra a URL para o Dify" "$(grep -q "http://172.17.0.1:18080/v1" <<<"${OUT}"; echo $?)"
}

# test_up_starts_stopped: container parado com o mesmo comando é só iniciado.
# Entrada: nenhuma. Saída: registra asserções via check.
test_up_starts_stopped() {
  setup_state stopped
  run_relay up
  check "up inicia o container parado" "$([[ ${RC} -eq 0 ]] && logged "docker start bonsai-relay" && ! logged "docker run"; echo $?)"
}

# test_up_recreates_changed: container com outro bind/destino é recriado.
# Entrada: nenhuma. Saída: registra asserções via check.
test_up_recreates_changed() {
  setup_state stopped "TCP-LISTEN:18080,bind=172.18.0.1,fork,reuseaddr TCP:127.0.0.1:8080"
  run_relay up
  check "up recria container com configuração antiga" "$([[ ${RC} -eq 0 ]] && logged "docker rm -f bonsai-relay" && logged "docker run"; echo $?)"
  check "up avisa que a URL mudou" "$(grep -q "configuração diferente" <<<"${OUT}"; echo $?)"
}

# test_up_already_running: no ar com o mesmo comando, nada é feito.
# Entrada: nenhuma. Saída: registra asserções via check.
test_up_already_running() {
  setup_state running
  run_relay up
  check "up não mexe no que já está no ar" "$([[ ${RC} -eq 0 ]] && ! logged "docker run" && ! logged "docker start"; echo $?)"
}

# test_up_warns_model_down: modelo fora do ar gera aviso, mas o relay sobe.
# Entrada: nenhuma. Saída: registra asserções via check.
test_up_warns_model_down() {
  setup_state absent
  MOCK_MODEL_UP=0 run_relay up
  check "up avisa que o modelo não responde" "$([[ ${RC} -eq 0 ]] && grep -q "AVISO.*modelo" <<<"${OUT}"; echo $?)"
}

# test_down: 'down' para o container no ar e não falha quando não há container.
# Entrada: nenhuma. Saída: registra asserções via check.
test_down() {
  setup_state running
  run_relay down
  check "down para o container" "$([[ ${RC} -eq 0 ]] && logged "docker stop bonsai-relay"; echo $?)"
  setup_state absent
  run_relay down
  check "down sem container não falha" "$([[ ${RC} -eq 0 ]] && ! logged "docker stop"; echo $?)"
}

# test_status: 'status' mostra o estado, a URL e a saúde do modelo.
# Entrada: nenhuma. Saída: registra asserções via check.
test_status() {
  setup_state running
  run_relay status
  check "status mostra relay no ar" "$([[ ${RC} -eq 0 ]] && grep -q "no ar" <<<"${OUT}"; echo $?)"
  check "status mostra a URL" "$(grep -q "http://172.17.0.1:18080/v1" <<<"${OUT}"; echo $?)"
  check "status mostra o modelo respondendo" "$(grep -q "modelo responde" <<<"${OUT}"; echo $?)"
  setup_state absent
  MOCK_MODEL_UP=0 run_relay status
  check "status sem container e sem modelo" "$([[ ${RC} -eq 0 ]] && grep -q "não existe" <<<"${OUT}" && grep -q "não responde" <<<"${OUT}"; echo $?)"
}

# test_status_with_old_config: no ar com outro bind, o status mostra a URL que vale agora e avisa.
# Entrada: nenhuma. Saída: registra asserções via check.
test_status_with_old_config() {
  setup_state running "TCP-LISTEN:18080,bind=172.18.0.1,fork,reuseaddr TCP:127.0.0.1:8080"
  run_relay status
  check "status mostra a URL do container em execução" "$(grep -q "http://172.18.0.1:18080/v1" <<<"${OUT}"; echo $?)"
  check "status avisa que o up recriaria" "$(grep -q "AVISO.*up" <<<"${OUT}" && grep -q "172.17.0.1" <<<"${OUT}"; echo $?)"
}

# test_custom_settings: variáveis mudam nome, porta, IP e destino.
# Entrada: nenhuma. Saída: registra asserções via check.
test_custom_settings() {
  setup_state absent
  LLM_RELAY_NAME=meu-relay LLM_RELAY_PORT=19000 LLM_RELAY_BIND_IP=10.0.0.5 LLM_RELAY_TARGET=127.0.0.1:1234 run_relay up
  check "variáveis personalizam o relay" "$(logged "docker run -d --name meu-relay --restart no --network host alpine/socat TCP-LISTEN:19000,bind=10.0.0.5,fork,reuseaddr TCP:127.0.0.1:1234" && ! logged "network inspect"; echo $?)"
}

# test_invalid_argument: sem comando válido, mostra o uso e falha.
# Entrada: nenhuma. Saída: registra asserções via check.
test_invalid_argument() {
  setup_state absent
  run_relay restart
  check "argumento inválido mostra o uso" "$([[ ${RC} -ne 0 ]] && grep -q "Uso:" <<<"${OUT}"; echo $?)"
}

test_up_creates
test_up_starts_stopped
test_up_recreates_changed
test_up_already_running
test_up_warns_model_down
test_down
test_status
test_status_with_old_config
test_custom_settings
test_invalid_argument

echo
if (( FAILURES > 0 )); then
  echo "${FAILURES} asserção(ões) falharam."
  exit 1
fi
echo "Todos os testes passaram."
