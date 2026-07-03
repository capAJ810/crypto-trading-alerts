FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alerts/ alerts/
COPY config.yaml .

# Check every 15 minutes; state persists via the volume in docker-compose.yml
CMD ["python", "-m", "alerts.watcher", "--state", "/data/state.json", "--loop", "900"]
