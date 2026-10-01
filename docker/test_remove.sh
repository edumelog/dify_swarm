#!/usr/bin/env bash
# Testes do docker/remove.sh com um `docker` simulado no PATH (nada é removido de verdade).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOVE="${SCRIPT_DIR}/remove.sh"
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

# Stub do docker. Estado em MOCK_DIR:
#   stack_up      existe enquanto a stack não foi removida
#   polls         quantas consultas ainda mostram restos da stack após o `stack rm`
#   volumes       nomes dos volumes da stack, um por linha
# MOCK_BUSY_VOLUME: volume que falha no `volume rm` (em uso).
mkdir -p "${WORK_DIR}/bin"
cat > "${WORK_DIR}/bin/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "${MOCK_DIR}/log"
# leftovers: imprime um resto da stack enquanto ela existe ou enquanto restam consultas em `polls`;
# só a consulta de serviços (a primeira de cada rodada) desconta uma consulta.
leftovers() {
  [[ -f "${MOCK_DIR}/stack_up" ]] && { echo "x"; return; }
  local n; n="$(cat "${MOCK_DIR}/polls" 2>/dev/null || echo 0)"
  (( n > 0 )) || return 0
  echo "x"
  [[ "$1" != "service" ]] || echo $((n - 1)) > "${MOCK_DIR}/polls"
}
case "$1 $2" in
  "stack ls") [[ -f "${MOCK_DIR}/stack_up" ]] && echo "${MOCK_STACK}"; true ;;
  "stack services") echo "${MOCK_STACK}_api 1/1"; echo "${MOCK_STACK}_web 1/1" ;;
  "stack rm") rm -f "${MOCK_DIR}/stack_up"; echo "${MOCK_RM_POLLS:-0}" > "${MOCK_DIR}/polls" ;;
  "service ls"|"network ls"|"config ls") leftovers "$1" ;;
  "volume ls") cat "${MOCK_DIR}/volumes" 2>/dev/null; true ;;
  "volume rm")
    shift 2
    for v in "$@"; do
      if [[ "${v}" == "${MOCK_BUSY_VOLUME:-}" ]]; then echo "Error: volume is in use - ${v}" >&2; exit 1; fi
      sed -i "/^${v}$/d" "${MOCK_DIR}/volumes"; echo "${v}"
    done ;;
esac
EOF
chmod +x "${WORK_DIR}/bin/docker"
export PATH="${WORK_DIR}/bin:${PATH}" MOCK_STACK="dify" REMOVE_POLL_SECONDS=0

# setup_state: prepara o estado simulado do Docker.
# Entrada: $1 "up" se a stack existe, $2.. nomes dos volumes da stack. Saída: MOCK_DIR preenchido.
setup_state() {
  export MOCK_DIR="${WORK_DIR}/state"
  rm -rf "${MOCK_DIR}"
  mkdir -p "${MOCK_DIR}"
  : > "${MOCK_DIR}/log"
  [[ "$1" != "up" ]] || touch "${MOCK_DIR}/stack_up"
  shift
  printf '%s\n' "$@" | sed '/^$/d' > "${MOCK_DIR}/volumes"
}

# run_remove: executa o remove.sh com a entrada $1.
# Entrada: $1 texto do stdin, $2.. argumentos. Saída: stdout+stderr em OUT e código em RC.
run_remove() {
  local input="$1"
  shift
  OUT="$(printf '%b' "${input}" | bash "${REMOVE}" "$@" 2>&1)"
  RC=$?
}

# logged: confere se o docker simulado recebeu um comando.
# Entrada: $1 trecho do comando. Saída: código 0 se encontrado.
logged() {
  grep -q -- "$1" "${MOCK_DIR}/log"
}

# test_keep_volumes: "n" remove a stack, espera a remoção e mantém os volumes.
# Entrada: nenhuma. Saída: registra asserções via check.
test_keep_volumes() {
  setup_state up dify_dify_postgres_data dify_dify_redis_data
  MOCK_RM_POLLS=2 run_remove "n\n"
  check "mantendo volumes termina com sucesso" "$([[ ${RC} -eq 0 ]]; echo $?)"
  check "lista os volumes antes de perguntar" "$(grep -q "dify_dify_postgres_data" <<<"${OUT}"; echo $?)"
  check "remove a stack" "$(logged "docker stack rm dify"; echo $?)"
  check "espera a remoção terminar" "$([[ "$(grep -c "docker service ls" "${MOCK_DIR}/log")" -ge 3 ]]; echo $?)"
  check "não apaga volumes" "$(! logged "docker volume rm"; echo $?)"
}

# test_delete_volumes: "s" e o nome correto da stack apagam os volumes depois da remoção.
# Entrada: nenhuma. Saída: registra asserções via check.
test_delete_volumes() {
  setup_state up dify_dify_postgres_data dify_redis_data
  run_remove "s\ndify\n"
  check "apagando volumes termina com sucesso" "$([[ ${RC} -eq 0 ]]; echo $?)"
  check "pede o nome da stack" "$(grep -q "Digite o nome da stack" <<<"${OUT}"; echo $?)"
  check "apaga os volumes da stack (inclusive antigos)" "$(logged "docker volume rm dify_dify_postgres_data dify_redis_data"; echo $?)"
  check "remove a stack antes dos volumes" "$([[ "$(grep -n "stack rm" "${MOCK_DIR}/log" | cut -d: -f1)" -lt "$(grep -n "volume rm" "${MOCK_DIR}/log" | cut -d: -f1)" ]]; echo $?)"
  check "filtra volumes pelo label da stack" "$(logged "volume ls -q --filter label=com.docker.stack.namespace=dify"; echo $?)"
}

# test_wrong_name_cancels: nome errado na confirmação cancela tudo, sem remover nada.
# Entrada: nenhuma. Saída: registra asserções via check.
test_wrong_name_cancels() {
  setup_state up dify_dify_postgres_data
  run_remove "s\nDIFY\n"
  check "nome errado falha" "$([[ ${RC} -ne 0 ]]; echo $?)"
  check "nome errado não remove a stack" "$(! logged "stack rm"; echo $?)"
  check "nome errado não apaga volumes" "$(! logged "volume rm"; echo $?)"
}

# test_invalid_answer_repeats: a pergunta dos volumes só aceita s/S/n/N.
# Entrada: nenhuma. Saída: registra asserções via check.
test_invalid_answer_repeats() {
  setup_state up dify_dify_postgres_data
  run_remove "\nsim\nN\n"
  check "repete a pergunta até receber s/S/n/N" "$([[ "$(grep -o "Apagar também os volumes" <<<"${OUT}" | wc -l)" -eq 3 ]]; echo $?)"
  check "N mantém os volumes" "$(logged "stack rm" && ! logged "volume rm"; echo $?)"
  setup_state up dify_dify_postgres_data
  run_remove ""
  check "sem resposta não remove nada" "$([[ ${RC} -ne 0 ]] && ! logged "stack rm"; echo $?)"
}

# test_volumes_without_stack: sem stack no ar, ainda oferece apagar os volumes que sobraram.
# Entrada: nenhuma. Saída: registra asserções via check.
test_volumes_without_stack() {
  setup_state down dify_dify_postgres_data
  run_remove "S\ndify\n"
  check "informa que a stack não está no ar" "$(grep -q "não está no ar" <<<"${OUT}"; echo $?)"
  check "não tenta remover stack inexistente" "$(! logged "stack rm"; echo $?)"
  check "apaga os volumes restantes" "$(logged "docker volume rm dify_dify_postgres_data"; echo $?)"
}

# test_nothing_to_remove: sem stack nem volumes, encerra sem perguntar.
# Entrada: nenhuma. Saída: registra asserções via check.
test_nothing_to_remove() {
  setup_state down
  run_remove ""
  check "nada a remover termina com sucesso" "$([[ ${RC} -eq 0 ]] && grep -q "Nada a remover" <<<"${OUT}"; echo $?)"
}

# test_no_volumes_skips_question: stack sem volumes é removida sem perguntar sobre volumes.
# Entrada: nenhuma. Saída: registra asserções via check.
test_no_volumes_skips_question() {
  setup_state up
  run_remove ""
  check "remove a stack sem volumes" "$([[ ${RC} -eq 0 ]] && logged "stack rm"; echo $?)"
  check "não pergunta sobre volumes" "$(! grep -q "Apagar também os volumes" <<<"${OUT}"; echo $?)"
}

# test_busy_volume_fails: volume em uso faz o script falhar indicando qual.
# Entrada: nenhuma. Saída: registra asserções via check.
test_busy_volume_fails() {
  setup_state up dify_dify_postgres_data
  MOCK_BUSY_VOLUME=dify_dify_postgres_data run_remove "s\ndify\n"
  check "volume em uso falha" "$([[ ${RC} -ne 0 ]] && grep -q "dify_dify_postgres_data" <<<"${OUT}"; echo $?)"
}

# test_custom_stack_name: STACK_NAME define a stack e o label dos volumes.
# Entrada: nenhuma. Saída: registra asserções via check.
test_custom_stack_name() {
  MOCK_STACK=difytest setup_state up difytest_dify_postgres_data
  MOCK_STACK=difytest STACK_NAME=difytest run_remove "s\ndifytest\n"
  check "remove a stack de STACK_NAME" "$(logged "docker stack rm difytest"; echo $?)"
  check "usa o label de STACK_NAME" "$(logged "label=com.docker.stack.namespace=difytest"; echo $?)"
}

# test_invalid_argument: argumentos não são aceitos.
# Entrada: nenhuma. Saída: registra asserções via check.
test_invalid_argument() {
  setup_state up
  run_remove "" --xyz
  check "falha com argumento inválido" "$([[ ${RC} -ne 0 ]] && grep -q "Uso:" <<<"${OUT}" && ! logged "stack rm"; echo $?)"
}

test_keep_volumes
test_delete_volumes
test_wrong_name_cancels
test_invalid_answer_repeats
test_volumes_without_stack
test_nothing_to_remove
test_no_volumes_skips_question
test_busy_volume_fails
test_custom_stack_name
test_invalid_argument

echo
if (( FAILURES > 0 )); then
  echo "${FAILURES} asserção(ões) falharam."
  exit 1
fi
echo "Todos os testes passaram."
