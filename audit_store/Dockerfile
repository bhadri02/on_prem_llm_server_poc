# ── Stage 1: builder ──────────────────────────────────────────────────────────
# Install all Python dependencies into /install so the final stage stays lean.
FROM python:3.12-slim AS builder

WORKDIR /build

# Upgrade pip then install dependencies into a prefix directory.
COPY requirements.txt .
RUN pip install --upgrade pip --no-cache-dir \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: final ────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Copy installed packages from the builder stage.
COPY --from=builder /install /usr/local

# Copy the application package.
COPY audit_store/ /app/audit_store/

WORKDIR /app

# Expose the API port and the Prometheus metrics port.
EXPOSE 9200
EXPOSE 9090

# Start both ASGI servers; wait keeps the container alive until either exits.
CMD ["sh", "-c", "uvicorn audit_store.main:app --host 0.0.0.0 --port 9200 & uvicorn audit_store.metrics_app:metrics_app --host 0.0.0.0 --port 9090 & wait"]
