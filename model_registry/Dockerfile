# model_registry/Dockerfile
# Build context: repo root  (docker build -f model_registry/Dockerfile .)
#
# Simple JSON file store — no ML packages. Builds fast.

FROM python:3.12-slim

WORKDIR /app

# Install dependencies (service + shared observability)
COPY model_registry/requirements.txt ./requirements.txt
COPY shared/requirements.txt         ./shared-requirements.txt

RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir -r shared-requirements.txt

# Copy application source
COPY model_registry/ ./model_registry/
COPY shared/         ./shared/

EXPOSE 5000 9090

ENV STORAGE_PATH=/data/models.json \
    LOG_LEVEL=INFO

CMD ["uvicorn", "model_registry.main:app", "--host", "0.0.0.0", "--port", "5000"]
