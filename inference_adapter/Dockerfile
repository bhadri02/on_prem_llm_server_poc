# inference_adapter/Dockerfile
# Build context: repo root  (docker build -f inference_adapter/Dockerfile .)
#
# Thin HTTP bridge to Ollama — no ML packages. Builds fast.

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY inference_adapter/requirements.txt ./requirements.txt
COPY shared/requirements.txt            ./shared-requirements.txt

RUN pip install --upgrade pip --no-cache-dir \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt \
 && pip install --no-cache-dir --prefix=/install -r shared-requirements.txt

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /install /usr/local

# Copy application source
COPY inference_adapter/ ./inference_adapter/
COPY shared/            ./shared/

EXPOSE 8087 9090

ENV PORT=8087 \
    METRICS_PORT=9090 \
    LOG_LEVEL=INFO \
    OLLAMA_BASE_URL=http://inference-ollama:11434 \
    DEFAULT_MODEL=llama3.2:3b \
    OLLAMA_TIMEOUT_SECONDS=120 \
    DEFAULT_MAX_TOKENS=2048 \
    MAX_TOKENS_LIMIT=4096 \
    DEFAULT_TEMPERATURE=0.7

# Metrics server is started as an asyncio background task inside main.py's lifespan —
# no separate metrics_app process needed.
CMD ["uvicorn", "inference_adapter.main:app", "--host", "0.0.0.0", "--port", "8087"]
