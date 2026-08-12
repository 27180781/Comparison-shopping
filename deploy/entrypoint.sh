#!/usr/bin/env bash
# One image, two roles, chosen by an environment variable.
#
# Caprover deploys an app from a repo and a branch, and both the API and the
# ingestion worker come from this one repo. Selecting the role here means both
# apps use the same captain-definition, the same Dockerfile and the same
# GitHub hook -- the only difference between them is ROLE=worker set in the
# Caprover UI. No command override, no second Dockerfile to keep in sync.
set -euo pipefail

case "${ROLE:-api}" in
  api)
    echo "[entrypoint] role=api"
    exec uvicorn api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
    ;;
  worker)
    echo "[entrypoint] role=worker"
    exec /app/deploy/worker-entrypoint.sh
    ;;
  *)
    echo "[entrypoint] ROLE must be 'api' or 'worker', got '${ROLE}'" >&2
    exit 64
    ;;
esac
