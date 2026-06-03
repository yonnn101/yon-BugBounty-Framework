"""Interface layer: asset graph view and ingestion endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_active_user, get_db
from models.user import User
from schemas.asset_actions import AssetIngestRequest
from schemas.discovery import SubdomainDiscoveryRequest, SubdomainDiscoveryResponse
from schemas.graph import GraphTreeNode, HierarchicalGraphView
from services import asset_service, program_service

router = APIRouter(tags=["assets"])


@router.get("/programs/{program_id}/graph", response_model=HierarchicalGraphView)
async def get_program_graph(
    program_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> HierarchicalGraphView:
    program = await program_service.get_program_for_owner(db, program_id, current_user.id)
    if program is None:
        raise HTTPException(status_code=404, detail="Program not found")
    roots_raw, orphans_raw = await asset_service.build_hierarchical_graph(db, program_id)
    roots = [GraphTreeNode.model_validate(n) for n in roots_raw]
    orphans = [GraphTreeNode.model_validate(n) for n in orphans_raw]
    return HierarchicalGraphView(program_id=program_id, roots=roots, orphans=orphans)


@router.post(
    "/programs/{program_id}/assets",
    status_code=status.HTTP_201_CREATED,
)
async def ingest_asset(
    program_id: uuid.UUID,
    body: AssetIngestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, str | None]:
    try:
        program = await program_service.get_program_for_owner(db, program_id, current_user.id)
        if program is None:
            raise HTTPException(status_code=404, detail="Program not found")
        child, rel = await asset_service.add_asset_with_relation(
            db,
            program_id,
            body.type,
            body.value,
            metadata=body.metadata,
            parent_asset_id=body.parent_asset_id,
            relation_type=body.relation_type,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "asset_id": str(child.id),
        "relation_id": str(rel.id) if rel else None,
    }


@router.post(
    "/programs/{program_id}/tasks/subdomain-discovery",
    response_model=SubdomainDiscoveryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_subdomain_discovery(
    program_id: uuid.UUID,
    body: SubdomainDiscoveryRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> SubdomainDiscoveryResponse:
    """Queue Subfinder + DNS resolution for the program (Celery worker must be running)."""
    program = await program_service.get_program_for_owner(db, program_id, current_user.id)
    if program is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Program not found")

    try:
        root = await asset_service.ensure_domain_asset_for_program(
            db,
            program_id,
            body.root_domain_asset_id,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "Root domain asset not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    domain = (body.domain or root.value or "").strip()
    if not domain:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Domain value is empty")

    from workers.celery_app import celery_app

    async_result = celery_app.send_task(
        "yonnn.discovery.process_subdomain_discovery",
        args=[str(program_id), str(body.root_domain_asset_id), domain],
        queue="slow",
    )
    return SubdomainDiscoveryResponse(task_id=async_result.id)
