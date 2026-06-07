"""Business logic: Asset Engine — upsert, correlation links, graph reads (spec §1 & §3).

Used by the API and Celery workers. Asset types and relation labels match
``models.enums.AssetType`` and ``RelationType`` (see specification.md).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.asset import Asset
from models.asset_relation import AssetRelation
from models.enums import AssetType, RelationType


def _normalize_asset_type(asset_type: str) -> str:
    """Map input to canonical ``AssetType`` value (e.g. ``DOMAIN``)."""
    raw = asset_type.strip()
    if not raw:
        msg = "Asset type cannot be empty"
        raise ValueError(msg)
    try:
        return AssetType(raw.upper()).value
    except ValueError as exc:
        allowed = ", ".join(m.value for m in AssetType)
        msg = f"Invalid asset type {asset_type!r}; allowed: {allowed}"
        raise ValueError(msg) from exc


def _normalize_relation_type(relation_type: str) -> str:
    """Map input to canonical ``RelationType`` value (e.g. ``resolves_to``)."""
    raw = relation_type.strip()
    if not raw:
        msg = "Relation type cannot be empty"
        raise ValueError(msg)
    try:
        return RelationType(raw).value
    except ValueError:
        pass
    try:
        return RelationType[raw.upper()].value
    except KeyError as exc:
        allowed = ", ".join(m.value for m in RelationType)
        msg = f"Invalid relation type {relation_type!r}; allowed: {allowed}"
        raise ValueError(msg) from exc


async def upsert_asset(
    session: AsyncSession,
    program_id: uuid.UUID,
    asset_type: str,
    value: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> Asset:
    """Insert or touch an asset by unique (program_id, type, value).

    * **Exists:** ``last_seen`` is refreshed; ``metadata`` keys are merged into JSONB.
    * **New:** row is created with ``first_seen`` / ``last_seen`` set to now.
    """
    try:
        norm_type = _normalize_asset_type(asset_type)
    except ValueError:
        logger.warning("upsert_asset: invalid asset_type={!r}", asset_type)
        raise

    norm_value = value.strip()
    if not norm_value:
        logger.warning("upsert_asset: empty value for program_id={}", program_id)
        msg = "Asset value cannot be empty"
        raise ValueError(msg)

    now = datetime.now(UTC)
    incoming_meta = dict(metadata) if metadata else {}

    try:
        stmt = select(Asset).where(
            Asset.program_id == program_id,
            Asset.type == norm_type,
            Asset.value == norm_value,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is not None:
            existing.last_seen = now
            if incoming_meta:
                merged = dict(existing.metadata_)
                merged.update(incoming_meta)
                existing.metadata_ = merged
            await session.flush()
            logger.debug(
                "upsert_asset: updated asset_id={} program_id={} type={}",
                existing.id,
                program_id,
                norm_type,
            )
            return existing

        asset = Asset(
            program_id=program_id,
            type=norm_type,
            value=norm_value,
            metadata_=incoming_meta,
            first_seen=now,
            last_seen=now,
        )
        session.add(asset)
        await session.flush()
        logger.debug(
            "upsert_asset: created asset_id={} program_id={} type={}",
            asset.id,
            program_id,
            norm_type,
        )
        return asset
    except ValueError:
        raise
    except Exception:
        logger.exception(
            "upsert_asset failed program_id={} type={!r} value_preview={!r}",
            program_id,
            norm_type,
            norm_value[:120],
        )
        raise


async def link_assets(
    session: AsyncSession,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    relation_type: str,
) -> AssetRelation:
    """Create a directed ``AssetRelation`` if none exists for (parent, child, type).

    Validates same-program endpoints and rejects self-links.
    """
    try:
        norm_rel = _normalize_relation_type(relation_type)
    except ValueError:
        logger.warning("link_assets: invalid relation_type={!r}", relation_type)
        raise

    if parent_id == child_id:
        logger.warning("link_assets: rejected self-relation for asset_id={}", parent_id)
        msg = "Cannot link an asset to itself"
        raise ValueError(msg)

    try:
        parent = await session.get(Asset, parent_id)
        child = await session.get(Asset, child_id)
        if parent is None:
            msg = f"Parent asset {parent_id} not found"
            logger.warning("link_assets: {}", msg)
            raise ValueError(msg)
        if child is None:
            msg = f"Child asset {child_id} not found"
            logger.warning("link_assets: {}", msg)
            raise ValueError(msg)
        if parent.program_id != child.program_id:
            logger.warning(
                "link_assets: program mismatch parent_program={} child_program={}",
                parent.program_id,
                child.program_id,
            )
            msg = "Parent and child assets must belong to the same program"
            raise ValueError(msg)

        rel_stmt = select(AssetRelation).where(
            AssetRelation.parent_id == parent_id,
            AssetRelation.child_id == child_id,
            AssetRelation.relation_type == norm_rel,
        )
        rel_result = await session.execute(rel_stmt)
        existing_rel = rel_result.scalar_one_or_none()
        if existing_rel is not None:
            logger.debug(
                "link_assets: duplicate skipped relation_id={} parent={} child={} type={}",
                existing_rel.id,
                parent_id,
                child_id,
                norm_rel,
            )
            return existing_rel

        relation = AssetRelation(
            parent_id=parent_id,
            child_id=child_id,
            relation_type=norm_rel,
        )
        session.add(relation)
        await session.flush()
        await session.refresh(relation)
        logger.debug(
            "link_assets: created relation_id={} parent={} child={} type={}",
            relation.id,
            parent_id,
            child_id,
            norm_rel,
        )
        return relation
    except ValueError:
        raise
    except Exception:
        logger.exception(
            "link_assets failed parent_id={} child_id={} relation_type={!r}",
            parent_id,
            child_id,
            relation_type,
        )
        raise


async def add_discovered_asset(
    session: AsyncSession,
    program_id: uuid.UUID,
    asset_type: str,
    value: str,
    *,
    metadata: dict[str, Any] | None = None,
    parent_id: uuid.UUID | None = None,
    relation_type: str | None = None,
) -> tuple[Asset, AssetRelation | None]:
    """Upsert the child asset and optionally link it from ``parent_id``.

    ``parent_id`` and ``relation_type`` must both be set or both omitted.
    The parent must exist and belong to ``program_id``.
    """
    has_parent = parent_id is not None
    has_rel = relation_type is not None
    if has_parent ^ has_rel:
        logger.warning(
            "add_discovered_asset: parent_id and relation_type must be provided together",
        )
        msg = "parent_id and relation_type must be provided together"
        raise ValueError(msg)

    try:
        child = await upsert_asset(
            session,
            program_id,
            asset_type,
            value,
            metadata=metadata,
        )

        if parent_id is None:
            return child, None

        parent = await session.get(Asset, parent_id)
        if parent is None:
            msg = f"Parent asset {parent_id} not found"
            logger.warning("add_discovered_asset: {}", msg)
            raise ValueError(msg)
        if parent.program_id != program_id:
            logger.warning(
                "add_discovered_asset: parent program mismatch expected={} actual={}",
                program_id,
                parent.program_id,
            )
            msg = "Parent asset belongs to a different program"
            raise ValueError(msg)

        relation = await link_assets(session, parent_id, child.id, relation_type)
        return child, relation
    except ValueError:
        raise
    except Exception:
        logger.exception(
            "add_discovered_asset failed program_id={} type={!r}",
            program_id,
            asset_type,
        )
        raise


async def get_or_create_asset(
    session: AsyncSession,
    program_id: uuid.UUID,
    asset_type: str,
    value: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> Asset:
    """Backward-compatible alias for :func:`upsert_asset`."""
    return await upsert_asset(
        session,
        program_id,
        asset_type,
        value,
        metadata=metadata,
    )


async def add_asset_with_relation(
    session: AsyncSession,
    program_id: uuid.UUID,
    asset_type: str,
    value: str,
    *,
    metadata: dict[str, Any] | None = None,
    parent_asset_id: uuid.UUID | None = None,
    relation_type: str | None = None,
) -> tuple[Asset, AssetRelation | None]:
    """API-oriented wrapper: same as :func:`add_discovered_asset` with ``parent_asset_id``."""
    return await add_discovered_asset(
        session,
        program_id,
        asset_type,
        value,
        metadata=metadata,
        parent_id=parent_asset_id,
        relation_type=relation_type,
    )


async def get_program_graph(
    session: AsyncSession,
    program_id: uuid.UUID,
) -> tuple[list[Asset], list[AssetRelation]]:
    """Load all assets for a program and relations whose endpoints are in that set."""
    try:
        assets_result = await session.execute(select(Asset).where(Asset.program_id == program_id))
        assets = list(assets_result.scalars().all())
        id_set = {a.id for a in assets}
        if not id_set:
            return [], []

        rel_result = await session.execute(
            select(AssetRelation).where(
                AssetRelation.parent_id.in_(id_set),
                AssetRelation.child_id.in_(id_set),
            )
        )
        edges = list(rel_result.scalars().all())
        return assets, edges
    except Exception:
        logger.exception("get_program_graph failed program_id={}", program_id)
        raise


async def ensure_domain_asset_for_program(
    session: AsyncSession,
    program_id: uuid.UUID,
    root_asset_id: uuid.UUID,
) -> Asset:
    """Validate root DOMAIN asset for subdomain discovery (raises ``ValueError``)."""
    root = await session.get(Asset, root_asset_id)
    if root is None:
        msg = "Root domain asset not found"
        raise ValueError(msg)
    if root.program_id != program_id:
        msg = "Root asset does not belong to this program"
        raise ValueError(msg)
    if root.type != AssetType.DOMAIN.value:
        msg = f"Root asset must be DOMAIN, got {root.type!r}"
        raise ValueError(msg)
    return root


# (parent_type, relation_type) -> sort tier (spec §3: DOMAIN→SUBDOMAIN→IP→PORT→SERVICE)
_REL_CHILD_ORDER: dict[tuple[str, str], int] = {
    (AssetType.DOMAIN.value, RelationType.CONTAINS.value): 10,
    (AssetType.DOMAIN.value, RelationType.RESOLVES_TO.value): 20,
    (AssetType.SUBDOMAIN.value, RelationType.CONTAINS.value): 10,
    (AssetType.SUBDOMAIN.value, RelationType.RESOLVES_TO.value): 20,
    (AssetType.IP.value, RelationType.HOSTS.value): 10,
    (AssetType.URL.value, RelationType.HOSTS.value): 15,
    (AssetType.PORT.value, RelationType.RUNS_ON.value): 10,
}


def _tree_node_payload(asset: Asset, children: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": asset.id,
        "type": asset.type,
        "value": asset.value,
        "metadata": dict(asset.metadata_ or {}),
        "first_seen": asset.first_seen,
        "last_seen": asset.last_seen,
        "children": children,
    }


async def build_hierarchical_graph(
    session: AsyncSession,
    program_id: uuid.UUID,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Discovery tree: root DOMAINs → SUBDOMAIN (contains) → IP (resolves_to) → PORT (hosts) → SERVICE (runs_on).

    Assets not reachable from any root DOMAIN are returned as **orphans** (flat nodes, empty children).
    """
    assets, edges = await get_program_graph(session, program_id)
    if not assets:
        return [], []

    by_id: dict[uuid.UUID, Asset] = {a.id: a for a in assets}
    adj: dict[uuid.UUID, list[tuple[str, uuid.UUID]]] = {}
    for e in edges:
        adj.setdefault(e.parent_id, []).append((e.relation_type, e.child_id))

    contained_children = {
        e.child_id for e in edges if e.relation_type == RelationType.CONTAINS.value
    }
    root_domains = sorted(
        (a for a in assets if a.type == AssetType.DOMAIN.value and a.id not in contained_children),
        key=lambda x: x.value.lower(),
    )

    visited: set[uuid.UUID] = set()

    def sort_outgoing(parent: Asset, pairs: list[tuple[str, uuid.UUID]]) -> list[tuple[str, uuid.UUID]]:
        def key(t: tuple[str, uuid.UUID]) -> tuple[int, str, str]:
            rel, cid = t
            ch = by_id.get(cid)
            if ch is None:
                return (999, "", "")
            tier = _REL_CHILD_ORDER.get((parent.type, rel), 50)
            return (tier, ch.type, ch.value.lower())

        return sorted(pairs, key=key)

    def build_subtree(aid: uuid.UUID) -> dict[str, Any] | None:
        asset = by_id.get(aid)
        if asset is None:
            return None
        visited.add(aid)
        raw = adj.get(aid, [])
        outgoing = sort_outgoing(asset, raw)
        child_payloads: list[dict[str, Any]] = []
        for _rel, cid in outgoing:
            if cid not in by_id:
                continue
            sub = build_subtree(cid)
            if sub is not None:
                child_payloads.append(sub)
        return _tree_node_payload(asset, child_payloads)

    roots_out: list[dict[str, Any]] = []
    for rd in root_domains:
        if rd.id in visited:
            continue
        node = build_subtree(rd.id)
        if node is not None:
            roots_out.append(node)

    orphans_assets = sorted(
        (a for a in assets if a.id not in visited),
        key=lambda x: (x.type, x.value.lower()),
    )
    orphans_out = [_tree_node_payload(a, []) for a in orphans_assets]
    return roots_out, orphans_out
