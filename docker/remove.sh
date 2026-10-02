#!/usr/bin/env bash
# Remove a stack do Dify no Docker Swarm e, se o operador escolher, apaga também os volumes
# dela (banco, storage, bases vetoriais etc.). Os volumes são achados pelo label
# com.docker.stack.namespace=<stack>, não pelo prefixo do nome.
# Com --compose, derruba o projeto Docker Compose (docker-compose.yaml + docker-compose.kb.yaml) e,
# se escolhido, apaga os volumes nomeados dele; os dados do Dify em docker/volumes/ nunca são apagados.
#
# Uso: docker/remove.sh [--swarm | --compose]
# Variáveis: STACK_NAME (padrão: dify), REMOVE_TIMEOUT (segundos de espera pela remoção; padrão: 300),
# REMOVE_POLL_SECONDS (intervalo entre as verificações; padrão: 3).
# Não mexe no .env, nos certificados nem na rede compartilhada com o NGPM.
set -euo pipefail

STACK_NAME="${STACK_NAME:-dify}"
REMOVE_TIMEOUT="${REMOVE_TIMEOUT:-300}"
REMOVE_POLL_SECONDS="${REMOVE_POLL_SECONDS:-3}"
STACK_LABEL="com.docker.stack.namespace=${STACK_NAME}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ARGS=(-f docker-compose.yaml -f docker-compose.kb.yaml)

# fail: escreve uma mensagem de erro em stderr e encerra com código 1.
# Entrada: $* mensagem. Saída: nenhuma (encerra o script).
fail() {
  echo "ERRO: $*" >&2
  exit 1
}

# usage: mostra a forma de uso em stderr.
# Entrada: nenhuma. Saída: texto de ajuda em stderr.
usage() {
  echo "Uso: $0 [--swarm | --compose]   (stack: STACK_NAME=${STACK_NAME}; padrão: --swarm)" >&2
}

# stack_exists: verifica se a stack está implantada no Swarm.
# Entrada: nenhuma. Saída: código 0 se a stack existir.
stack_exists() {
  docker stack ls --format '{{.Name}}' | grep -qx "${STACK_NAME}"
}

# stack_volumes: lista os volumes criados pela stack, pelo label do namespace.
# Entrada: nenhuma. Saída: nomes dos volumes em stdout, um por linha.
stack_volumes() {
  docker volume ls -q --filter "label=${STACK_LABEL}"
}

# stack_leftovers: conta serviços, redes e configs da stack que ainda existem.
# Entrada: nenhuma. Saída: quantidade em stdout.
stack_leftovers() {
  {
    docker service ls -q --filter "label=${STACK_LABEL}"
    docker network ls -q --filter "label=${STACK_LABEL}"
    docker config ls -q --filter "label=${STACK_LABEL}"
  } | grep -c . || true
}

# wait_stack_removed: espera a remoção assíncrona da stack terminar.
# Entrada: nenhuma. Saída: nenhuma; encerra com erro se passar de REMOVE_TIMEOUT segundos.
wait_stack_removed() {
  local start=${SECONDS}
  echo "Aguardando a remoção de serviços, redes e configs..." >&2
  while (( $(stack_leftovers) > 0 )); do
    (( SECONDS - start < REMOVE_TIMEOUT )) \
      || fail "a stack '${STACK_NAME}' não terminou de ser removida em ${REMOVE_TIMEOUT}s; confira com 'docker stack ps ${STACK_NAME}'."
    sleep "${REMOVE_POLL_SECONDS}"
  done
  echo "Stack '${STACK_NAME}' removida." >&2
}

# ask_delete_volumes: pergunta se os volumes também devem ser apagados, sem resposta padrão.
# Entrada: respostas no stdin (só s/S/n/N; qualquer outra repete a pergunta). Saída: código 0 para sim, 1 para não; encerra se a entrada acabar.
ask_delete_volumes() {
  local answer
  while true; do
    printf 'Apagar também os volumes (dados)? [s/n] ' >&2
    read -r answer || fail "nenhuma resposta; nada foi removido."
    case "${answer}" in
      s|S) return 0 ;;
      n|N) return 1 ;;
      *) echo "Responda s ou n." >&2 ;;
    esac
  done
}

# confirm_volume_deletion: exige que o operador digite o nome (da stack ou do projeto) antes de apagar dados.
# Entrada: $1 nome esperado, $2 o que ele é ("da stack" ou "do projeto"); resposta no stdin.
# Saída: nenhuma; encerra sem remover nada se o nome não conferir.
confirm_volume_deletion() {
  local expected="${1:-${STACK_NAME}}" kind="${2:-da stack}" answer=""
  echo "ATENÇÃO: os volumes guardam o banco, os arquivos enviados e as bases de conhecimento; a exclusão não pode ser desfeita." >&2
  printf 'Digite o nome %s (%s) para confirmar a exclusão dos volumes: ' "${kind}" "${expected}" >&2
  read -r answer || true
  [[ "${answer}" == "${expected}" ]] || fail "confirmação não confere; nada foi removido."
}

# delete_volumes: apaga os volumes informados.
# Entrada: $@ nomes dos volumes. Saída: volumes apagados listados em stderr; encerra com erro se algum estiver em uso.
delete_volumes() {
  docker volume rm "$@" >/dev/null \
    || fail "não foi possível apagar todos os volumes (algum ainda em uso?); restantes: $(stack_volumes | tr '\n' ' ')"
  printf '  - %s\n' "$@" >&2
  echo "${#} volume(s) apagado(s)." >&2
}

# compose: roda o docker compose com os arquivos do Dify e do kb, na pasta docker/.
# Entrada: $@ subcomando e argumentos. Saída: a do docker compose.
compose() {
  (cd "${SCRIPT_DIR}" && docker compose "${COMPOSE_ARGS[@]}" "$@")
}

# compose_project: descobre o nome do projeto Compose (padrão: nome da pasta, ou COMPOSE_PROJECT_NAME).
# Entrada: nenhuma. Saída: nome em stdout.
compose_project() {
  compose config 2>/dev/null | sed -n 's/^name: //p' | head -1
}

# remove_compose: mostra o que existe, pergunta sobre os volumes e derruba o projeto Compose.
# Entrada: respostas no stdin. Saída: código 0 em sucesso.
remove_compose() {
  local project containers volumes=() delete=0
  project="$(compose_project)"
  [[ -n "${project}" ]] || fail "não foi possível ler o projeto Compose (docker-compose.yaml + docker-compose.kb.yaml)."
  containers="$(compose ps -a --format '{{.Name}} {{.State}}' 2>/dev/null || true)"
  mapfile -t volumes < <(docker volume ls -q --filter "label=com.docker.compose.project=${project}")
  if [[ -z "${containers}" && ${#volumes[@]} -eq 0 ]]; then
    echo "Nada a remover: o projeto Compose '${project}' não tem containers nem volumes." >&2
    exit 0
  fi
  if [[ -n "${containers}" ]]; then
    echo "Containers do projeto Compose '${project}':" >&2
    sed 's/^/  - /' <<<"${containers}" >&2
  fi
  if (( ${#volumes[@]} > 0 )); then
    echo "Volumes nomeados do projeto:" >&2
    printf '  - %s\n' "${volumes[@]}" >&2
    if ask_delete_volumes; then
      confirm_volume_deletion "${project}" "do projeto"
      delete=1
    fi
  fi
  if (( delete )); then
    compose down -v >&2
    echo "Containers removidos e volumes nomeados apagados." >&2
  else
    compose down >&2
    echo "Containers removidos; volumes mantidos." >&2
  fi
  echo "Os dados do Dify no modo Compose ficam em docker/volumes/ (banco, Redis, Weaviate, arquivos) e não são apagados por este script." >&2
}

# main: mostra o que existe, pergunta sobre os volumes, remove a stack e, se escolhido, os volumes.
# Entrada: $@ argumentos (--swarm, padrão, ou --compose). Saída: código 0 em sucesso.
main() {
  case "${1:-}" in
    ""|--swarm) ;;
    --compose) (( $# == 1 )) || { usage; exit 1; }; remove_compose; return 0 ;;
    *) usage; exit 1 ;;
  esac
  (( $# <= 1 )) || { usage; exit 1; }
  local has_stack=0 delete=0 volumes=()
  stack_exists && has_stack=1
  mapfile -t volumes < <(stack_volumes)

  if (( ! has_stack && ${#volumes[@]} == 0 )); then
    echo "Nada a remover: a stack '${STACK_NAME}' não está no ar e não há volumes dela." >&2
    exit 0
  fi
  if (( has_stack )); then
    echo "Stack '${STACK_NAME}' no ar, serviços:" >&2
    docker stack services "${STACK_NAME}" --format '{{.Name}} {{.Replicas}}' | sed 's/^/  - /' >&2
  else
    echo "A stack '${STACK_NAME}' não está no ar." >&2
  fi
  if (( ${#volumes[@]} > 0 )); then
    echo "Volumes da stack:" >&2
    printf '  - %s\n' "${volumes[@]}" >&2
    if ask_delete_volumes; then
      confirm_volume_deletion
      delete=1
    fi
  fi

  if (( has_stack )); then
    docker stack rm "${STACK_NAME}" >&2
    wait_stack_removed
  fi
  if (( delete )); then
    delete_volumes "${volumes[@]}"
  elif (( ${#volumes[@]} > 0 )); then
    echo "Volumes mantidos: um novo deploy com o mesmo STACK_NAME reaproveita os dados (e as senhas gravadas no banco)." >&2
  fi
}

main "$@"
