# audit_store/Dockerfile
# Build context: repo root  (docker build -f audit_store/Dockerfile .)
#
# Pure SQLite service — no ML packages. Builds fast.

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY audit_store/requirements.txt ./requirements.txt
COPY shared/requirements.txt      ./shared-requirements.txt

RUN pip install --upgrade pip --no-cache-dir \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt \
 && pip install --no-cache-dir --prefix=/install -r shared-requirements.txt

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /install /usr/local

# Copy application source
COPY audit_store/ ./audit_store/
COPY shared/      ./shared/

EXPOSE 9200 9090

CMD ["sh", "-c", \
     "uvicorn audit_store.main:app --host 0.0.0.0 --port 9200 & \
      uvicorn audit_store.metrics_app:metrics_app --host 0.0.0.0 --port 9090 & \
      wait"]
