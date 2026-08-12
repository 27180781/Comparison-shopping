#!/usr/bin/env bash
# Health for whichever role this container is running.
#
# The API answer is obvious. The worker one is the reason this file exists: a
# container with no HTTP server fails an HTTP health check, and Docker Swarm
# answers a failing health check by restarting the task -- in the middle of a
# download measured in gigabytes, forever.
set -uo pipefail

if [[ "${ROLE:-api}" == "worker" ]]; then
  # The first cycle after a deploy runs before cron takes over and can last
  # half an hour. The marker covers that window so the worker is not killed
  # while it is doing exactly what it was deployed to do.
  [[ -f /tmp/ingestion-starting ]] && exit 0
  pgrep -x cron > /dev/null || exit 1
  exit 0
fi

curl -fsS "http://localhost:${PORT:-8000}/health" > /dev/null || exit 1
