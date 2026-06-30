FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY model_registry/ model_registry/

EXPOSE 5000

ENV STORAGE_PATH=/data/models.json
ENV LOG_LEVEL=INFO

CMD ["uvicorn", "model_registry.main:app", "--host", "0.0.0.0", "--port", "5000"]
