# Admin Portal — Layer 10 Portal_API (POC)
# Multi-stage build: installs dependencies in a builder stage then copies only
# the installed packages and app source into the final image.
# Runs as UID 1000 (non-root); compatible with securityContext.runAsNonRoot: true.
#
# Build context must be the repo root so the full admin_portal/ package is available:
#   docker build -f admin_portal/Dockerfile -t admin-portal:latest .

# ---------------------------------------------------------------------------
# Stage 1 — builder: install Python dependencies into an isolated prefix
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# Copy only the requirements file first so this layer is cached until it changes
COPY admin_portal/requirements.txt .

# Install all packages into /install so they can be copied cleanly to the final stage
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2 — final: minimal runtime image
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Create a non-root user (UID 1000) before copying app files
RUN adduser --uid 1000 --disabled-password --gecos "" appuser

WORKDIR /app

# Copy the application package (full admin_portal/ package from repo root)
COPY admin_portal/ ./admin_portal/

# Transfer ownership so appuser can read the app files
RUN chown -R appuser:appuser /app

# Switch to non-root user for runtime
USER appuser

# Portal_API HTTP port
EXPOSE 8084

# Prometheus metrics port (served via a separate ASGI mount in main.py)
EXPOSE 9090

# Start the Portal_API with uvicorn
CMD ["uvicorn", "admin_portal.main:app", "--host", "0.0.0.0", "--port", "8084"]
