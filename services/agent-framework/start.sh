#!/bin/sh
# start.sh — Start the Agent Framework service.
#
# Launches two uvicorn instances in the same process:
#   - Main API app on port 8083 (background)
#   - Prometheus metrics app on port 9090 (foreground)
#
# The main app runs in the background (&) so both processes start together.
# The metrics app runs in the foreground so the container stays alive as long
# as the metrics app is running. If either process dies the container should
# be restarted by the orchestrator.

set -e

# Start main API in background
uvicorn agent_framework.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8083}" &

# Start Prometheus metrics app in foreground
exec uvicorn agent_framework.main:metrics_app \
    --host 0.0.0.0 \
    --port "${METRICS_PORT:-9090}"
