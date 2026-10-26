FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY girls_agent ./girls_agent
# The database lives in /data: mount a persistent volume there so data survives restarts
ENV DB_PATH=/data/girls_agent.sqlite3 PYTHONUNBUFFERED=1
RUN mkdir -p /data
CMD ["python", "-m", "girls_agent.bot"]
