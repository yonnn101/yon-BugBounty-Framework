"""Debug / health tasks: verify worker ↔ Redis ↔ PostgreSQL."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from loguru import logger

from models.enums import JobStatus
from services.job_service import sync_mark_job_status
from workers.base_task import AsyncBaseTask
from workers.celery_app import celery_app


@celery_app.task(
    bind=True,
    base=AsyncBaseTask,
    name="yonnn.debug.ping",
    queue="fast",
    max_retries=0,
    autoretry_for=(),
)
def debug_ping(
    self,
    job_id: str | None = None,
    scan_options: dict | None = None,
) -> str:
    """Execute ``SELECT 1`` via async session; return ``pong`` if Redis + DB are reachable."""

    job_uuid: uuid.UUID | None = None
    if job_id:
        try:
            job_uuid = uuid.UUID(job_id)
        except ValueError:
            job_uuid = None
    if job_uuid is not None:
        sync_mark_job_status(job_uuid, JobStatus.RUNNING.value)

    async def work(session: AsyncSession) -> str:
        await session.execute(text("SELECT 1"))
        logger.info("debug_ping OK task_id={}", self.request.id)
        return "pong"

    try:
        out = self.run_with_session(work)
        if job_uuid is not None:
            sync_mark_job_status(job_uuid, JobStatus.COMPLETED.value)
        return out
    except Exception as exc:
        if job_uuid is not None:
            sync_mark_job_status(
                job_uuid,
                JobStatus.FAILED.value,
                error_detail=str(exc)[:2000],
            )
        raise
