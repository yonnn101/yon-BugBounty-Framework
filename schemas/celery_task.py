"""Validation layer: Celery async result snapshots for the UI."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CeleryTaskStatus(BaseModel):
    """Broker-backed task state (same Redis as the worker)."""

    task_id: str
    state: str
    result: Any = Field(None, description="Decoded when state is SUCCESS")
    error: str | None = Field(None, description="Message when state is FAILURE")
