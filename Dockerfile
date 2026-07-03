FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alerts/ alerts/
COPY config.yaml .

# Check every 3 minutes; state persists via the volume in docker-compose.yml
CMD ["python", "-m", "alerts.watcher", "--state", "/data/state.json", \
     "--tg-state", "/data/telegram.json", "--loop", "180"]
