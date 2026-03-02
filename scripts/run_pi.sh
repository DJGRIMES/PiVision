#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VENV_PATH="$ROOT/.venv"
ENV_FILE="$ROOT/.env.deploy"

if [ ! -f "$VENV_PATH/bin/activate" ]; then
  echo "virtualenv is missing; run 'make setup' first" >&2
  exit 1
fi

source "$VENV_PATH/bin/activate"

if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

case "${1:-}" in
  server)
    exec python -m backend.server
    ;;
  worker)
    exec python -m backend.worker
    ;;
  retention)
    exec python scripts/retention.py
    ;;
  check)
    exec scripts/check_backend.sh
    ;;
  *)
    cat <<'EOF'
Usage: scripts/run_pi.sh <command>

Commands:
  server    start the ingest API server
  worker    start the analysis worker
  retention run the nightly retention cleanup
  check     run the ready/health validation script
EOF
    exit 1
    ;;
esac
