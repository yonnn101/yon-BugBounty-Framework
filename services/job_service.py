"""Business logic: scan/job rows for Celery correlation (spec §1, §2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from models.asset import Asset
from models.enums import JobStatus
from models.job import Job
from models.program import Program


async def create_job(
    session: AsyncSession,
    *,
    tool_name: str,
    target_asset_id: uuid.UUID | None,
    status: str = JobStatus.PENDING.value,
    workflow_instance_id: uuid.UUID | None = None,
    workflow_type: str | None = None,
    workflow_step_index: int | None = None,
    scan_options: dict | None = None,
) -> Job:
    """Insert a job row (caller commits)."""
    job = Job(
        tool_name=tool_name,
        status=status,
        target_asset_id=target_asset_id,
        workflow_instance_id=workflow_instance_id,
        workflow_type=workflow_type,
        workflow_step_index=workflow_step_index,
        scan_options=scan_options,
    )
    session.add(job)
    await session.flush()
    await session.refresh(job)
    return job


async def set_celery_task_id(
    session: AsyncSession,
    job_id: uuid.UUID,
    celery_task_id: str,
) -> Job | None:
    job = await session.get(Job, job_id)
    if job is None:
        return None
    job.celery_task_id = celery_task_id
    await session.flush()
    return job


async def mark_job_status(
    session: AsyncSession,
    job_id: uuid.UUID,
    status: str,
    *,
    error_detail: str | None = None,
) -> Job | None:
    """Update lifecycle fields; sets start_time on running, end_time on terminal states."""
    job = await session.get(Job, job_id)
    if job is None:
        logger.warning("mark_job_status: job not found job_id={}", job_id)
        return None
    now = datetime.now(UTC)
    job.status = status
    if status == JobStatus.RUNNING.value and job.start_time is None:
        job.start_time = now
    if status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
        job.end_time = now
    if error_detail and status == JobStatus.FAILED.value:
        logger.error(
            "mark_job_status job_id={} failed: {}",
            job_id,
            error_detail,
        )
    await session.flush()
    return job


def sync_mark_job_status(
    job_id: uuid.UUID,
    status: str,
    *,
    error_detail: str | None = None,
) -> None:
    """Celery worker (sync context): open a short-lived session and commit status."""
    import asyncio

    from core.database import AsyncSessionLocal

    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            await mark_job_status(session, job_id, status, error_detail=error_detail)
            await session.commit()

    asyncio.run(_run())


def sync_create_job(
    *,
    tool_name: str,
    target_asset_id: uuid.UUID | None,
    status: str = JobStatus.PENDING.value,
    workflow_instance_id: uuid.UUID | None = None,
    workflow_type: str | None = None,
    workflow_step_index: int | None = None,
    scan_options: dict | None = None,
) -> uuid.UUID:
    """Worker (sync): persist a job row and return its id."""
    import asyncio

    from core.database import AsyncSessionLocal

    async def _run() -> uuid.UUID:
        async with AsyncSessionLocal() as session:
            job = await create_job(
                session,
                tool_name=tool_name,
                target_asset_id=target_asset_id,
                status=status,
                workflow_instance_id=workflow_instance_id,
                workflow_type=workflow_type,
                workflow_step_index=workflow_step_index,
                scan_options=scan_options,
            )
            jid = job.id
            await session.commit()
            return jid

    return asyncio.run(_run())


def sync_set_celery_task_id(job_id: uuid.UUID, celery_task_id: str) -> None:
    import asyncio

    from core.database import AsyncSessionLocal

    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            await set_celery_task_id(session, job_id, celery_task_id)
            await session.commit()

    asyncio.run(_run())


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await session.get(Job, job_id)


async def get_job_for_owner(
    session: AsyncSession,
    job_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> Job | None:
    """Resolve job if its target asset's program is owned by ``owner_id``."""
    stmt = (
        select(Job)
        .options(joinedload(Job.target_asset).joinedload(Asset.program))
        .where(Job.id == job_id)
    )
    result = await session.execute(stmt)
    job = result.unique().scalar_one_or_none()
    if job is None:
        return None
    if job.target_asset_id is None:
        return None
    asset = job.target_asset
    if asset is None:
        return None
    prog: Program | None = asset.program
    if prog is None or prog.owner_id != owner_id:
        return None
    return job
