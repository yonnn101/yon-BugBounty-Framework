"""Interface layer: launch Celery-backed scans and read Job status (spec §2)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_active_user, get_db
from models.user import User
from schemas.scan import ScanLaunchRequest, ScanLaunchResponse, ScanStatusResponse
from services import scan_service

router = APIRouter(prefix="/scans", tags=["scans"])


@router.post("/launch", response_model=ScanLaunchResponse, status_code=status.HTTP_202_ACCEPTED)
async def launch_scan(
    body: ScanLaunchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> ScanLaunchResponse:
    """Create a Job row and enqueue the matching Celery task (see ``scan_service`` registry)."""
    try:
        job_id, celery_task_id, workflow_instance_id, resolved_seq, bridges = await scan_service.launch_scan(
            db,
            current_user.id,
            asset_id=body.asset_id,
            tool_name=body.tool_name,
            workflow_type=body.workflow_type,
            workflow_sequence=body.workflow_sequence,
            auto_bridge_workflow=body.auto_bridge_workflow,
            options=body.options,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ScanLaunchResponse(
        job_id=job_id,
        celery_task_id=celery_task_id,
        workflow_instance_id=workflow_instance_id,
        resolved_workflow_sequence=resolved_seq,
        bridges_inserted=bridges,
    )


@router.get("/status/{job_id}", response_model=ScanStatusResponse)
async def get_scan_status(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> ScanStatusResponse:
    """Return Job status; merges Celery result state when ``celery_task_id`` is present."""
    merged = await scan_service.get_merged_scan_status(db, current_user.id, job_id)
    if not merged:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return ScanStatusResponse.from_merged(merged)
