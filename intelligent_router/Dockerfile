# intelligent_router/Dockerfile
# Build context: repo root  (docker build -f intelligent_router/Dockerfile .)

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY intelligent_router/requirements.txt ./requirements.txt
COPY shared/requirements.txt             ./shared-requirements.txt

RUN pip install --upgrade pip --no-cache-dir \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt \
 && pip install --no-cache-dir --prefix=/install -r shared-requirements.txt

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /install /usr/local

# Copy application source
COPY intelligent_router/ ./intelligent_router/
COPY shared/             ./shared/

# YAML config files live at repo root — baked into image
COPY model_matrix.yaml           ./model_matrix.yaml
COPY task_classifier_rules.yaml  ./task_classifier_rules.yaml

# Non-root user. Note: docker-compose.prod.yml bind-mounts the routable
# model/policy/task-classifier YAML read-only over these baked-in copies —
# those host files must stay world-readable (standard git checkout perms)
# for appuser to read them.
RUN adduser --uid 1000 --disabled-password --gecos "" appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8082 9090

CMD ["sh", "-c", \
     "uvicorn intelligent_router.main:app --host 0.0.0.0 --port 8082 & \
      uvicorn intelligent_router.metrics_app:metrics_app --host 0.0.0.0 --port 9090 & \
      wait"]
