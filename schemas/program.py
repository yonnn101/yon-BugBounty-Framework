"""Validation layer: Pydantic models for Program API payloads."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProgramBase(BaseModel):
    """Shared program fields."""

    name: str = Field(..., min_length=1, max_length=255)
    platform: str = Field(default="H1", max_length=16)
    reward_type: str | None = Field(default=None, max_length=64)
    in_scope: list | dict = Field(default_factory=list)
    out_scope: list | dict = Field(default_factory=list)
    settings: dict = Field(default_factory=dict)


class ProgramCreate(ProgramBase):
    """Request body to create a program."""


class ProgramUpdate(BaseModel):
    """Partial update for a program."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    platform: str | None = Field(default=None, max_length=16)
    reward_type: str | None = Field(default=None, max_length=64)
    in_scope: list | dict | None = None
    out_scope: list | dict | None = None
    settings: dict | None = None


class ProgramRead(ProgramBase):
    """Program returned to clients."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ProgramSummaryStats(BaseModel):
    """Aggregate counts for dashboard / program list."""

    total_assets: int = 0
    assets_by_type: dict[str, int] = Field(default_factory=dict)


class ProgramReadWithStats(ProgramRead):
    """Program row including asset inventory summary."""

    summary: ProgramSummaryStats
