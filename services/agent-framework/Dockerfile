# services/agent-framework/Dockerfile
# Build context: repo root  (docker build -f services/agent-framework/Dockerfile .)
#
# Agent Framework's agent_framework sub-package DOES import shared.observability.
# Heavy packages: langchain-openai, langgraph, langchain-core.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY services/agent-framework/requirements.txt ./requirements.txt
COPY shared/requirements.txt                   ./shared-requirements.txt

RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir -r shared-requirements.txt

# Copy application source
COPY services/agent-framework/ ./

# Copy shared observability package (imported by agent_framework sub-package)
COPY shared/ ./shared/

# Ensure start.sh is executable and has Unix line endings (strip Windows \r)
RUN sed -i 's/\r$//' ./start.sh && chmod +x ./start.sh

ENV PYTHONPATH=/app \
    PORT=8083 \
    METRICS_PORT=9090

EXPOSE 8083 9090

CMD ["/app/start.sh"]
