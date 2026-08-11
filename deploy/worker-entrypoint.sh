#!/usr/bin/env bash
# Ingestion worker: apply migrations, run once so a fresh deploy has data
# immediately, then hand over to cron.
set -euo pipefail

echo "[worker] applying migrations"
alembic upgrade head

if [[ "${RUN_ON_START:-true}" == "true" ]]; then
  echo "[worker] initial ingestion cycle"
  # A failure here must not stop the container: cron will retry on schedule,
  # and a crash loop would mean no ingestion at all rather than a late one.
  python -m ingestion cycle || echo "[worker] initial cycle failed; cron will retry"
fi

echo "[worker] starting cron"
crontab /app/deploy/crontab
exec cron -f
