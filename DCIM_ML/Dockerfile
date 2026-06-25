# DCIM_ML — Python FastAPI capacity-planning service
FROM python:3.11-slim

# libgomp1 is required by xgboost / lightgbm at runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY app ./app

# Persisted models live here (mounted volume in compose)
ENV MODEL_PATH=/app/models
RUN mkdir -p /app/models

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=5 --start-period=20s \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
