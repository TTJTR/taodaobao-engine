#!/bin/sh
# Keep this file LF-only; it is the Linux container entrypoint.
set -eu

echo "Applying database migrations..."
alembic upgrade head

echo "Starting FastAPI application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
