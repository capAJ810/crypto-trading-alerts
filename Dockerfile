FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alerts/ alerts/
COPY config.yaml .

# Candle-aligned session that exits daily; compose's restart policy loops it.
# State persists via the volume in docker-compose.yml.
CMD ["python", "-m", "alerts.watcher", "--state", "/data/state.json", \
     "--tg-state", "/data/telegram.json", "--run-for", "86400"]
