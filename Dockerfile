# Newsfeed: 72-hour news aggregator. No port is published; see compose.yaml.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    NEWSFEED_DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY newsfeed ./newsfeed

RUN useradd --system --uid 10001 --home-dir /app newsfeed \
    && mkdir -p /data \
    && chown newsfeed:newsfeed /data
USER newsfeed

VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz', timeout=4)"]

# Exec form so SIGTERM reaches uvicorn for a clean shutdown (SQLite is closed on exit).
CMD ["python", "-m", "newsfeed"]
