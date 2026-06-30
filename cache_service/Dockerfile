FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cache_service/ cache_service/

EXPOSE 8086 9090

ENV PORT=8086 \
    LOG_LEVEL=INFO \
    REDIS_URL=redis://redis:6379 \
    EMBEDDING_MODEL=all-MiniLM-L6-v2

CMD ["uvicorn", "cache_service.main:app", "--host", "0.0.0.0", "--port", "8086"]
