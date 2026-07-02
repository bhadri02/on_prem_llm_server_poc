from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel


class ModelRecord(BaseModel):
    name: str
    version: str
    backend: str
    tasks: List[str]
    status: Literal["active", "retired", "staging"]


class ModelStatusPatch(BaseModel):
    status: Literal["active", "retired", "staging"]
