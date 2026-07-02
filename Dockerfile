FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY service/config/sources.json ./shared-config/sources.json

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
