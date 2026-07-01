FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY inference_adapter/ inference_adapter/

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

CMD ["uvicorn", "inference_adapter.main:app", "--host", "0.0.0.0", "--port", "8087"]
