#!/usr/bin/env bash
# Roda os testes do kb_admin (pytest) num container Python 3.12, montando o código atual.
# Uso: docker/kb_admin/run_tests.sh [argumentos do pytest]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_IMAGE="dify-kb-admin:test"

docker build -q --target test -t "${TEST_IMAGE}" "${SCRIPT_DIR}" >/dev/null
docker run --rm -v "${SCRIPT_DIR}:/app" -w /app "${TEST_IMAGE}" \
  python -m pytest -p no:cacheprovider "$@"
