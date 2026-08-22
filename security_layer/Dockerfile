# security_layer/Dockerfile
# Build context: repo root  (docker build -f security_layer/Dockerfile .)
#
# gcc is needed at build time for Presidio's Rust/C extension compilation.
# The final runtime image does NOT include gcc — multi-stage keeps the image lean.

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY security_layer/requirements.txt ./requirements.txt
COPY shared/requirements.txt         ./shared-requirements.txt

RUN pip install --upgrade pip --no-cache-dir \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt \
 && pip install --no-cache-dir --prefix=/install -r shared-requirements.txt

# Download the spaCy English model required by Presidio.
# Use en_core_web_sm (12MB) baked at build time — application code
# explicitly loads this model so no outbound download occurs at runtime.
RUN PYTHONPATH=/install/lib/python3.12/site-packages \
    python -m spacy download en_core_web_sm --target /install/lib/python3.12/site-packages

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /install /usr/local

# Copy application source
COPY security_layer/     ./security_layer/
COPY shared/             ./shared/

# injection_patterns.yaml lives at repo root
COPY injection_patterns.yaml ./injection_patterns.yaml

# Non-root user
RUN adduser --uid 1000 --disabled-password --gecos "" appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8081 9090

CMD ["sh", "-c", \
     "uvicorn security_layer.main:app --host 0.0.0.0 --port 8081 & \
      uvicorn security_layer.metrics_app:metrics_app --host 0.0.0.0 --port 9090 & \
      wait"]
