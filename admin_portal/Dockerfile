# admin_portal/Dockerfile
# Build context: repo root  (docker build -f admin_portal/Dockerfile .)
#
# Admin Portal does NOT import shared.observability (uses its own JSON logger).
# No ML packages — builds fast.

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

COPY admin_portal/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=builder /install /usr/local

# Non-root user
RUN adduser --uid 1000 --disabled-password --gecos "" appuser

# Copy application source (admin_portal only — no shared/ dependency)
COPY admin_portal/ ./admin_portal/

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8084 9090

CMD ["uvicorn", "admin_portal.main:app", "--host", "0.0.0.0", "--port", "8084"]
