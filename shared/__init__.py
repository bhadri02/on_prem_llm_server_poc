# shared/__init__.py
# Makes 'shared' a Python package so services can import:
#   from shared.observability.logging import configure_structlog, get_logger, emit
#   from shared.observability.metrics import make_layer_metrics
#   from shared.observability.middleware import LoggingMiddleware, configure_tracing
