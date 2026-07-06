# api_gateway/Dockerfile
# Build context: repo root  (docker build -f api_gateway/Dockerfile .)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies (service-specific + shared observability)
COPY api_gateway/requirements.txt ./requirements.txt
COPY shared/requirements.txt      ./shared-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir -r shared-requirements.txt

# Copy application source
COPY api_gateway/ ./api_gateway/

# Copy shared observability package (imported as shared.observability)
COPY shared/ ./shared/

EXPOSE 8080 9090

# api_gateway mounts Prometheus at /metrics on port 8080 — no separate metrics process.
CMD ["uvicorn", "api_gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]
