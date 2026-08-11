# Runtime for both the API and the ingestion worker. One image, two commands --
# they share the models, the field map and the config, so splitting them would
# mean keeping two builds honest for no benefit.
FROM python:3.13-slim

# lxml needs libxml2/libxslt at runtime; the wheels bundle their own but the
# slim image lacks the loader dependencies. tzdata because every timestamp is
# rendered in Asia/Jerusalem.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
        tzdata \
        curl \
        cron \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Jerusalem \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The scraper writes here; on Caprover this should be a persistent volume so a
# restart mid-run does not re-download gigabytes.
RUN mkdir -p /app/dumps
VOLUME ["/app/dumps"]

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
