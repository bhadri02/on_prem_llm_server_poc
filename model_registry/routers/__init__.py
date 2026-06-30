"""
Routers sub-package for the Model Registry.

Contains the FastAPI APIRouter instances for the /models and /health endpoints.
"""

from model_registry.routers.models import router as models_router

__all__ = ["models_router"]
