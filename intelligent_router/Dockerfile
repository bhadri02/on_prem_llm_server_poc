# ── Stage 1: builder ──────────────────────────────────────────────────────────
# Install all Python dependencies into /install so the final stage stays lean.
FROM python:3.12-slim AS builder

WORKDIR /build

# Upgrade pip then install dependencies into a prefix directory.
COPY requirements.txt .
RUN pip install --upgrade pip --no-cache-dir \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

# Copy installed packages from the builder stage.
COPY --from=builder /install /usr/local

# Set working directory.
WORKDIR /app

# Copy the application package.
COPY intelligent_router/ /app/intelligent_router/

# Copy config YAML files (located at repo root).
COPY task_classifier_rules.yaml /app/task_classifier_rules.yaml
COPY model_matrix.yaml /app/model_matrix.yaml

# Expose the API port and the Prometheus metrics port.
EXPOSE 8082
EXPOSE 9090

# Start both ASGI servers; wait keeps the container alive until either exits.
CMD ["sh", "-c", "uvicorn intelligent_router.main:app --host 0.0.0.0 --port 8082 & uvicorn intelligent_router.metrics_app:metrics_app --host 0.0.0.0 --port 9090 & wait"]
