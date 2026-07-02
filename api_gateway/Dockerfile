# API Gateway — Layer 1 (POC)
# Python 3.11-slim base image keeps the image lean for POC use.

FROM python:3.11-slim

# Prevent .pyc files and enable unbuffered stdout/stderr for structured logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (layer cached separately from source)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application package
COPY api_gateway/ ./api_gateway/

# API Gateway listens on port 8080 (configurable via PORT env var)
EXPOSE 8080

# Run with uvicorn; PORT and LOG_LEVEL are injected by the Helm chart
CMD ["uvicorn", "api_gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]
