from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    error: Literal[
        "validation_error",
        "not_found",
        "upstream_unavailable",
        "internal_error",
    ]
    message: str  # human-readable description
    upstream: Optional[
        Literal["api-gateway", "audit-store", "model-registry", "prometheus"]
    ] = None
    allowed_values: Optional[List[str]] = None  # for enum validation errors
