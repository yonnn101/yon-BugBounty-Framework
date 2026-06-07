"""Validation layer: remote scan launch and job status (spec §2)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ScanLaunchRequest(BaseModel):
    """Enqueue a single tool, a named workflow, or a custom tool sequence against a target asset."""

    asset_id: uuid.UUID
    tool_name: str | None = Field(default=None, min_length=1, max_length=128)
    workflow_type: str | None = Field(default=None, min_length=1, max_length=64)
    workflow_sequence: list[str] | None = Field(
        default=None,
        description="Logical tool ids (e.g. subfinder, dns_resolve, httpx_stub). Validated for I/O compatibility.",
    )
    auto_bridge_workflow: bool = Field(
        default=True,
        description="If true, insert known bridges (e.g. dns_resolve between subfinder and IP scanners).",
    )
    options: dict[str, Any] | None = Field(
        default=None,
        description="Merged with Program.settings; use intelligence toggles or nested {'intelligence': {...}}.",
    )

    @model_validator(mode="after")
    def exclusive_launch_mode(self) -> ScanLaunchRequest:
        has_tool = self.tool_name is not None and self.tool_name.strip() != ""
        has_named = self.workflow_type is not None and self.workflow_type.strip() != ""
        has_custom = self.workflow_sequence is not None and len(self.workflow_sequence) > 0
        n = sum(1 for x in (has_tool, has_named, has_custom) if x)
        if n != 1:
            raise ValueError("Provide exactly one of: tool_name, workflow_type, or workflow_sequence")
        return self


class ScanLaunchResponse(BaseModel):
    """Persistent job id plus broker task id for correlation."""

    job_id: uuid.UUID
    celery_task_id: str
    workflow_instance_id: uuid.UUID | None = Field(
        default=None,
        description="Shared id for all Jobs in a multi-step workflow chain.",
    )
    resolved_workflow_sequence: list[str] | None = Field(
        default=None,
        description="After validation / auto-bridging (custom workflow_sequence launches only).",
    )
    bridges_inserted: list[str] | None = Field(
        default=None,
        description="Bridge step ids auto-inserted (e.g. dns_resolve) when auto_bridge_workflow is true.",
    )


class ScanStatusResponse(BaseModel):
    """Job row merged with optional Celery result-backend state."""

    job_id: uuid.UUID
    tool_name: str
    status: str
    target_asset_id: uuid.UUID | None = None
    start_time: str | None = None
    end_time: str | None = None
    celery_task_id: str | None = None
    celery_state: str | None = None
    celery_error: str | None = None
    workflow_instance_id: str | None = None
    workflow_type: str | None = None
    workflow_step_index: int | None = None
    scan_options: dict[str, Any] | None = None

    @classmethod
    def from_merged(cls, data: dict[str, Any]) -> ScanStatusResponse:
        return cls(
            job_id=uuid.UUID(data["job_id"]),
            tool_name=data["tool_name"],
            status=data["status"],
            target_asset_id=uuid.UUID(data["target_asset_id"]) if data.get("target_asset_id") else None,
            start_time=data.get("start_time"),
            end_time=data.get("end_time"),
            celery_task_id=data.get("celery_task_id"),
            celery_state=data.get("celery_state"),
            celery_error=data.get("celery_error"),
            workflow_instance_id=data.get("workflow_instance_id"),
            workflow_type=data.get("workflow_type"),
            workflow_step_index=data.get("workflow_step_index"),
            scan_options=data.get("scan_options"),
        )
