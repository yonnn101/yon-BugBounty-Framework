"""Placeholder / router-friendly Celery tasks for multi-step workflows (config-driven chains)."""

from __future__ import annotations

from typing import Any

from loguru import logger

from models.enums import JobStatus
from services.job_service import sync_mark_job_status
from workers.base_task import AsyncBaseTask
from workers.celery_app import celery_app


@celery_app.task(
    bind=True,
    base=AsyncBaseTask,
    name="yonnn.workflow.http_probe_stub",
    queue="slow",
    max_retries=0,
    autoretry_for=(),
)
def http_probe_stub(
    self,
    program_id: str,
    job_id: str | None = None,
    workflow_name: str | None = None,
    workflow_instance_id: str | None = None,
    workflow_step_index: int = 0,
    scan_options: dict[str, Any] | None = None,
    root_target_asset_id: str | None = None,
) -> dict[str, Any]:
    """Stand-in for httpx / web probe; extend with real tooling later."""

    logger.info(
        "http_probe_stub program_id={} workflow={} step={}",
        program_id,
        workflow_name,
        workflow_step_index,
    )
    out = {"program_id": program_id, "stub": "http_probe", "status": "skipped_stub"}
    if job_id:
        try:
            from uuid import UUID

            sync_mark_job_status(UUID(job_id), JobStatus.COMPLETED.value)
        except Exception:
            logger.exception("http_probe_stub: job status update failed")
    if workflow_name and workflow_instance_id and root_target_asset_id:
        from services.workflow_service import trigger_next_step

        trigger_next_step(
            out,
            workflow_name,
            workflow_step_index,
            job_id=job_id,
            workflow_instance_id=workflow_instance_id,
            scan_options=scan_options,
            root_target_asset_id=root_target_asset_id,
        )
    return out


@celery_app.task(
    bind=True,
    base=AsyncBaseTask,
    name="yonnn.workflow.tool_stub",
    queue="slow",
    max_retries=0,
    autoretry_for=(),
)
def tool_stub(
    self,
    previous: dict[str, Any] | None = None,
    step_label: str = "",
    job_id: str | None = None,
    workflow_name: str | None = None,
    workflow_instance_id: str | None = None,
    workflow_step_index: int = 0,
    scan_options: dict[str, Any] | None = None,
    root_target_asset_id: str | None = None,
) -> dict[str, Any]:
    """Generic chain step for vuln_scan demos; replace with nmap/nuclei tasks later."""

    logger.info(
        "tool_stub label={} workflow={} step={}",
        step_label,
        workflow_name,
        workflow_step_index,
    )
    # Empty results → workflow router skips nuclei (see ``_skip_nuclei_after_empty_nmap``).
    results: list[str] = []
    out: dict[str, Any] = {
        "step": step_label,
        "results": results,
        "previous_keys": list((previous or {}).keys()),
    }
    if job_id:
        try:
            from uuid import UUID

            sync_mark_job_status(UUID(job_id), JobStatus.COMPLETED.value)
        except Exception:
            logger.exception("tool_stub: job status update failed")
    if workflow_name and workflow_instance_id and root_target_asset_id:
        from services.workflow_service import trigger_next_step

        trigger_next_step(
            out,
            workflow_name,
            workflow_step_index,
            job_id=job_id,
            workflow_instance_id=workflow_instance_id,
            scan_options=scan_options,
            root_target_asset_id=root_target_asset_id,
        )
    return out
