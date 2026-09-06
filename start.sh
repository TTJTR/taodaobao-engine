#!/bin/sh
# Keep this file LF-only; it is the Linux container entrypoint.
set -eu

echo "Applying database migrations..."
alembic upgrade head

echo "Starting FastAPI application..."
exec uvicorn app.main:app \
  --host "${APP_HOST:-127.0.0.1}" \
  --port "${APP_PORT:-8000}" \
  --log-level "$(printf '%s' "${APP_LOG_LEVEL:-INFO}" | tr '[:upper:]' '[:lower:]')" \
  --no-access-log
