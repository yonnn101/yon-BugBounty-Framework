"""Business logic: program CRUD (scope containers for assets), scoped by owner."""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.asset import Asset
from models.program import Program
from services import intelligence_service


async def create_program(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    name: str,
    platform: str = "H1",
    reward_type: str | None = None,
    in_scope: list | dict | None = None,
    out_scope: list | dict | None = None,
    settings: dict | None = None,
) -> Program:
    """Create a program owned by ``owner_id`` (request-scoped session commits via ``get_db``)."""
    merged_settings = dict(settings or {})
    intel_defaults = intelligence_service.default_program_intelligence_settings()
    existing_intel = merged_settings.get("intelligence")
    if isinstance(existing_intel, dict):
        merged_intel = {**intel_defaults, **{k: bool(v) for k, v in existing_intel.items() if k in intel_defaults}}
    else:
        merged_intel = dict(intel_defaults)
    merged_settings["intelligence"] = merged_intel

    program = Program(
        owner_id=owner_id,
        name=name,
        platform=platform,
        reward_type=reward_type,
        in_scope=in_scope if in_scope is not None else [],
        out_scope=out_scope if out_scope is not None else [],
        settings=merged_settings,
    )
    session.add(program)
    await session.flush()
    await session.refresh(program)
    return program


async def list_programs(session: AsyncSession, owner_id: uuid.UUID) -> Sequence[Program]:
    """Return programs owned by the given user, ordered by name."""
    result = await session.execute(
        select(Program).where(Program.owner_id == owner_id).order_by(Program.name),
    )
    return result.scalars().all()


async def list_programs_with_asset_stats(
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> list[tuple[Program, int, dict[str, int]]]:
    """Each program with total asset count and per-``AssetType`` breakdown."""
    programs = list(await list_programs(session, owner_id))
    if not programs:
        return []
    pids = [p.id for p in programs]
    stmt = (
        select(Asset.program_id, Asset.type, func.count(Asset.id))
        .where(Asset.program_id.in_(pids))
        .group_by(Asset.program_id, Asset.type)
    )
    rows = await session.execute(stmt)
    by_type: dict[uuid.UUID, dict[str, int]] = {pid: {} for pid in pids}
    totals: dict[uuid.UUID, int] = {pid: 0 for pid in pids}
    for pid, typ, cnt in rows:
        c = int(cnt)
        by_type[pid][str(typ)] = c
        totals[pid] += c
    return [(p, totals[p.id], by_type[p.id]) for p in programs]


async def get_program(session: AsyncSession, program_id: uuid.UUID) -> Program | None:
    """Fetch a program by id (no ownership check — use :func:`get_program_for_owner` from routes)."""
    result = await session.execute(select(Program).where(Program.id == program_id))
    return result.scalar_one_or_none()


async def get_program_for_owner(
    session: AsyncSession,
    program_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> Program | None:
    """Return the program only if it belongs to ``owner_id``."""
    result = await session.execute(
        select(Program).where(Program.id == program_id, Program.owner_id == owner_id),
    )
    return result.scalar_one_or_none()


async def update_program(
    session: AsyncSession,
    program_id: uuid.UUID,
    owner_id: uuid.UUID,
    *,
    name: str | None = None,
    platform: str | None = None,
    reward_type: str | None = None,
    in_scope: list | dict | None = None,
    out_scope: list | dict | None = None,
    settings: dict | None = None,
) -> Program | None:
    """Patch fields on a program; returns None if missing or not owned."""
    program = await get_program_for_owner(session, program_id, owner_id)
    if program is None:
        return None
    if name is not None:
        program.name = name
    if platform is not None:
        program.platform = platform
    if reward_type is not None:
        program.reward_type = reward_type
    if in_scope is not None:
        program.in_scope = in_scope
    if out_scope is not None:
        program.out_scope = out_scope
    if settings is not None:
        merged = {**(program.settings or {}), **settings}
        intel_defaults = intelligence_service.default_program_intelligence_settings()
        prev_i = merged.get("intelligence") if isinstance(merged.get("intelligence"), dict) else {}
        merged["intelligence"] = {
            **intel_defaults,
            **{k: bool(v) for k, v in prev_i.items() if k in intel_defaults},
        }
        program.settings = merged
    await session.flush()
    await session.refresh(program)
    return program


async def delete_program(session: AsyncSession, program_id: uuid.UUID, owner_id: uuid.UUID) -> bool:
    """Delete program if owned by ``owner_id`` (cascades to assets)."""
    program = await get_program_for_owner(session, program_id, owner_id)
    if program is None:
        return False
    await session.delete(program)
    return True
