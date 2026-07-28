# cache_service/Dockerfile
# Build context: repo root  (docker build -f cache_service/Dockerfile .)

FROM python:3.12-slim

WORKDIR /app

# Install dependencies (service + shared)
COPY cache_service/requirements.txt ./requirements.txt
COPY shared/requirements.txt        ./shared-requirements.txt

# Install torch CPU-only first so sentence-transformers doesn't pull CUDA torch (~7 GB).
# This keeps the image around ~1.5 GB instead of 8.5 GB.
RUN pip install --no-cache-dir \
      torch==2.5.1+cpu \
      torchvision==0.20.1+cpu \
      --extra-index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir -r shared-requirements.txt

# Pre-download the sentence-transformers embedding model at build time so the
# container doesn't need outbound internet access at runtime.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# Copy application source
COPY cache_service/ ./cache_service/
COPY shared/        ./shared/

EXPOSE 8086 9090

ENV PORT=8086 \
    LOG_LEVEL=INFO

# Metrics server is started as an asyncio background task inside main.py's lifespan —
# no separate metrics_app process needed.
CMD ["uvicorn", "cache_service.main:app", "--host", "0.0.0.0", "--port", "8086"]
