# One image, two roles. The API and the ingestion worker share the models, the
# field map and the config, so splitting them would mean keeping two builds
# honest for no benefit. Which one a container becomes is decided at runtime by
# ROLE (see deploy/entrypoint.sh), which is what lets both Caprover apps deploy
# from this single repo, Dockerfile and GitHub hook.
FROM python:3.13-slim

# lxml needs libxml2/libxslt at runtime; the wheels bundle their own but the
# slim image lacks the loader dependencies. tzdata because every timestamp is
# rendered in Asia/Jerusalem. procps for pgrep, which is how the worker's
# health check knows cron is alive.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
        tzdata \
        curl \
        cron \
        procps \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Jerusalem \
    PIP_NO_CACHE_DIR=1 \
    ROLE=api

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Git preserves the mode, but a tarball uploaded by a CI runner may not.
RUN chmod +x /app/deploy/*.sh

# The scraper writes here; on Caprover this should be a persistent volume so a
# restart mid-run does not re-download gigabytes.
RUN mkdir -p /app/dumps
VOLUME ["/app/dumps"]

EXPOSE 8000

# start-period covers migrations on the worker and the first import on the API.
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD /app/deploy/healthcheck.sh

CMD ["/app/deploy/entrypoint.sh"]
