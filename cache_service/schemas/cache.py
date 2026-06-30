"""
Pydantic schemas for cache-specific request/response contracts.

Defines:
  CacheBlock      — the IMF `cache` sub-object written on every lookup response
  LookupResponse  — response body for POST /cache/lookup
  WriteResponse   — response body for POST /cache/write
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from cache_service.schemas.imf import IMFResponse


class CacheBlock(BaseModel):
    """
    The `cache` block embedded in the IMF document on every lookup response.

    This block is always replaced wholesale — never merged with the incoming value.
    """

    lookup_hit: bool
    cache_key: str
    cache_type: Literal["exact", "semantic"] | None
    similarity_score: float | None = None


class LookupResponse(BaseModel):
    """Response body returned by POST /cache/lookup."""

    hit: bool
    cache_key: str
    cache_type: Literal["exact", "semantic"] | None
    response: IMFResponse | None
    similarity_score: float | None = None


class WriteResponse(BaseModel):
    """Response body returned by POST /cache/write."""

    written: bool
    cache_key: str
