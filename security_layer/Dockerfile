# ──────────────────────────────────────────────────────────────────────────────
# Stage 1 — builder: install Python deps into an isolated prefix
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies (needed by some Presidio transitive deps)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip \
    && pip install --prefix=/install --no-cache-dir -r requirements.txt

# ──────────────────────────────────────────────────────────────────────────────
# Stage 2 — runtime: lean image with only what is needed to run the service
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy application source
COPY security_layer/ ./security_layer/

# Copy injection patterns configuration
COPY injection_patterns.yaml .

# Expose API port and Prometheus metrics port
EXPOSE 8081 9090

# Run both ASGI apps in the same container (POC single-process pattern)
# - Main app on port 8081
# - Metrics app on port 9090
CMD ["sh", "-c", "uvicorn security_layer.main:app --host 0.0.0.0 --port 8081 & uvicorn security_layer.metrics_app:metrics_app --host 0.0.0.0 --port 9090 & wait"]
