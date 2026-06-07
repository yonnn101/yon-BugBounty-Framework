"""Business logic: map tool names to Celery tasks and persist Job rows (spec §2)."""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from models.asset import Asset
from models.enums import AssetType, JobStatus
from models.program import Program
from services import intelligence_service, job_service, workflow_service


class _ToolSpec(TypedDict):
    task_name: str
    queue: str


_TOOL_REGISTRY: dict[str, _ToolSpec] = {
    "subdomain_discovery": {
        "task_name": "yonnn.discovery.process_subdomain_discovery",
        "queue": "slow",
    },
    "worker_health": {
        "task_name": "yonnn.debug.ping",
        "queue": "fast",
    },
}


def list_scan_tool_names() -> list[str]:
    return sorted(_TOOL_REGISTRY.keys())


async def _asset_owned_by(
    session: AsyncSession,
    asset_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> Asset | None:
    stmt = (
        select(Asset)
        .options(joinedload(Asset.program))
        .where(Asset.id == asset_id)
    )
    result = await session.execute(stmt)
    asset = result.unique().scalar_one_or_none()
    if asset is None:
        return None
    prog: Program | None = asset.program
    if prog is None or prog.owner_id != owner_id:
        return None
    return asset


async def launch_scan(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    asset_id: uuid.UUID,
    tool_name: str | None = None,
    workflow_type: str | None = None,
    workflow_sequence: list[str] | None = None,
    auto_bridge_workflow: bool = True,
    options: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, str, uuid.UUID | None, list[str] | None, list[str] | None]:
    """Create a Job (or workflow), enqueue Celery.

    Returns ``(job_id, celery_task_id, workflow_instance_id, resolved_sequence, bridges_inserted)``.
    Last two entries are set for custom ``workflow_sequence`` launches only.

    ``options`` is merged with ``Program.settings['intelligence']`` for feature toggles (see ``intelligence_service``).

    Raises ``ValueError`` with a client-safe message on invalid input.
    """
    asset = await _asset_owned_by(session, asset_id, owner_id)
    if asset is None:
        msg = "Asset not found or not accessible"
        raise ValueError(msg)

    program = asset.program
    if program is None:
        msg = "Program not found for asset"
        raise ValueError(msg)

    merged_opts = intelligence_service.merge_scan_options_for_job(program.settings, options)

    if workflow_sequence is not None and len(workflow_sequence) > 0:
        job_id, wf_instance_id, celery_task_id, resolved, bridge_log = (
            await workflow_service.start_custom_workflow(
                session,
                program=program,
                target_asset=asset,
                scan_options=merged_opts,
                tool_sequence=workflow_sequence,
                auto_bridge=auto_bridge_workflow,
            )
        )
        logger.info(
            "launch_scan custom_sequence job_id={} workflow_instance_id={} resolved={} bridges={}",
            job_id,
            wf_instance_id,
            resolved,
            bridge_log,
        )
        return job_id, celery_task_id, wf_instance_id, resolved, bridge_log or None

    if workflow_type is not None and workflow_type.strip():
        job_id, wf_instance_id, celery_task_id = await workflow_service.start_workflow(
            session,
            workflow_name=workflow_type.strip(),
            program=program,
            target_asset=asset,
            scan_options=merged_opts,
        )
        logger.info(
            "launch_scan workflow={} job_id={} workflow_instance_id={} celery_task_id={}",
            workflow_type.strip().lower(),
            job_id,
            wf_instance_id,
            celery_task_id,
        )
        return job_id, celery_task_id, wf_instance_id, None, None

    if tool_name is None or not tool_name.strip():
        msg = "Provide tool_name, workflow_type, or workflow_sequence"
        raise ValueError(msg)

    key = tool_name.strip().lower().replace("-", "_")
    spec = _TOOL_REGISTRY.get(key)
    if spec is None:
        tools = ", ".join(list_scan_tool_names())
        flows = ", ".join(workflow_service.list_workflow_names())
        msg = f"Unknown tool_name {tool_name!r}; tools: [{tools}]. For chains use workflow_type: [{flows}]"
        raise ValueError(msg)

    job = await job_service.create_job(
        session,
        tool_name=key,
        target_asset_id=asset_id,
        status=JobStatus.PENDING.value,
        scan_options=merged_opts,
    )
    await session.flush()

    from workers.celery_app import celery_app

    celery_task_id: str
    if key == "subdomain_discovery":
        if asset.type != AssetType.DOMAIN.value:
            msg = "subdomain_discovery requires a DOMAIN asset"
            raise ValueError(msg)
        domain = (asset.value or "").strip()
        if not domain:
            msg = "DOMAIN asset has empty value"
            raise ValueError(msg)
        async_result = celery_app.send_task(
            spec["task_name"],
            args=[str(asset.program_id), str(asset.id), domain],
            kwargs={"job_id": str(job.id), "scan_options": merged_opts},
            queue=spec["queue"],
        )
        celery_task_id = async_result.id
    elif key == "worker_health":
        async_result = celery_app.send_task(
            spec["task_name"],
            args=[],
            kwargs={"job_id": str(job.id), "scan_options": merged_opts},
            queue=spec["queue"],
        )
        celery_task_id = async_result.id
    else:
        msg = f"Tool {key!r} is not wired to Celery"
        raise ValueError(msg)

    await job_service.set_celery_task_id(session, job.id, celery_task_id)
    logger.info(
        "launch_scan job_id={} celery_task_id={} tool={} asset_id={}",
        job.id,
        celery_task_id,
        key,
        asset_id,
    )
    return job.id, celery_task_id, None, None, None


async def get_merged_scan_status(
    session: AsyncSession,
    owner_id: uuid.UUID,
    job_id: uuid.UUID,
) -> dict[str, Any]:
    """Return Job row fields plus optional Celery state when ``celery_task_id`` is set."""
    job = await job_service.get_job_for_owner(session, job_id, owner_id)
    if job is None:
        return {}

    out: dict[str, Any] = {
        "job_id": str(job.id),
        "tool_name": job.tool_name,
        "status": job.status,
        "target_asset_id": str(job.target_asset_id) if job.target_asset_id else None,
        "start_time": job.start_time.isoformat() if job.start_time else None,
        "end_time": job.end_time.isoformat() if job.end_time else None,
        "celery_task_id": job.celery_task_id,
        "celery_state": None,
        "celery_error": None,
        "workflow_instance_id": str(job.workflow_instance_id) if job.workflow_instance_id else None,
        "workflow_type": job.workflow_type,
        "workflow_step_index": job.workflow_step_index,
        "scan_options": job.scan_options,
    }

    if job.celery_task_id:
        from workers.celery_app import celery_app

        ar = celery_app.AsyncResult(job.celery_task_id)
        out["celery_state"] = ar.state
        if ar.state == "FAILURE":
            try:
                out["celery_error"] = str(ar.result)
            except Exception:
                out["celery_error"] = "failure"
        if ar.state in ("STARTED", "RETRY") and job.status == JobStatus.PENDING.value:
            await job_service.mark_job_status(session, job.id, JobStatus.RUNNING.value)
            out["status"] = JobStatus.RUNNING.value
        if ar.state == "SUCCESS" and job.status not in (
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
        ):
            await job_service.mark_job_status(session, job.id, JobStatus.COMPLETED.value)
            out["status"] = JobStatus.COMPLETED.value
        elif ar.state == "FAILURE" and job.status not in (
            JobStatus.FAILED.value,
            JobStatus.COMPLETED.value,
        ):
            await job_service.mark_job_status(
                session,
                job.id,
                JobStatus.FAILED.value,
                error_detail=out.get("celery_error") or "celery failure",
            )
            out["status"] = JobStatus.FAILED.value

    return out
