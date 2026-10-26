FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/tlavrova/girls-agent" \
      org.opencontainers.image.description="Telegram bot that organizes meetups for a group of friends" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # The database lives in /data: mount a persistent volume there so data survives restarts
    DB_PATH=/data/girls_agent.sqlite3

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Run as an unprivileged user; a named volume mounted at /data inherits this ownership
RUN useradd --create-home --uid 1000 bot && mkdir -p /data && chown bot:bot /data
COPY girls_agent ./girls_agent
USER bot

VOLUME ["/data"]
CMD ["python", "-m", "girls_agent.bot"]
