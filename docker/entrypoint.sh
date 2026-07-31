#!/bin/sh
set -eu

role="${1:-api}"

case "$role" in
  api)
    exec uvicorn ai_gateway.api.app:create_app --factory --host "${AIGW_HOST:-0.0.0.0}" --port "${AIGW_PORT:-8000}"
    ;;
  worker)
    exec python -m ai_gateway.workers.main
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    exec "$@"
    ;;
esac
