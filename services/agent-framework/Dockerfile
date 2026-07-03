FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Make start.sh executable
RUN chmod +x /app/start.sh

ENV PYTHONPATH=/app
ENV PORT=8083
ENV METRICS_PORT=9090

EXPOSE 8083 9090

CMD ["/app/start.sh"]
