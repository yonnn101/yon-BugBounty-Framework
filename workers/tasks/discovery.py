"""Subdomain discovery (Subfinder) → DNS resolution chain (spec §2, §3)."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid
from pathlib import Path
from typing import Any

from celery import chain
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.base_tool import AsyncBaseTool
from models.asset import Asset
from models.enums import AssetType, JobStatus, RelationType, ToolDatumKind
from services.asset_service import add_discovered_asset, link_assets, upsert_asset
from services.job_service import sync_mark_job_status
from workers.base_task import AsyncBaseTask
from workers.celery_app import celery_app

DNS_RESOLVE_BATCH_SIZE = max(1, int(os.environ.get("DNS_RESOLVE_BATCH_SIZE", "20")))
SUBFINDER_BINARY = os.environ.get("SUBFINDER_PATH", "subfinder")


class SubfinderTask(AsyncBaseTool):
    """Runs ``subfinder -d <domain> -json`` and parses host rows."""

    tool_name = "subfinder"
    INPUT_TYPES = frozenset({ToolDatumKind.DOMAIN})
    OUTPUT_TYPES = frozenset({ToolDatumKind.SUBDOMAIN})

    def __init__(self, binary_path: str | Path | None = None, **kwargs: Any) -> None:
        super().__init__(binary_path or SUBFINDER_BINARY, **kwargs)

    def parse_output(self, output_string: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        text = (output_string or "").strip()
        if not text:
            return rows

        if text.startswith("["):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        rows.append(item)
                    elif isinstance(item, str):
                        rows.append({"host": item})
                return rows

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                rows.append({"host": line})
                continue
            if isinstance(obj, dict):
                rows.append(obj)
            elif isinstance(obj, str):
                rows.append({"host": obj})

        return rows


def _host_from_row(row: dict[str, Any]) -> str:
    for key in ("host", "subdomain", "name"):
        val = row.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().lower().rstrip(".")
    return ""


async def _resolve_host_ips(hostname: str) -> list[str]:
    """Blocking ``getaddrinfo`` off the event loop; returns unique A/AAAA strings."""

    def _lookup() -> list[str]:
        try:
            infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except OSError:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for info in infos:
            addr = info[4][0]
            if addr not in seen:
                seen.add(addr)
                out.append(addr)
        return out

    return await asyncio.to_thread(_lookup)


@celery_app.task(
    bind=True,
    base=AsyncBaseTask,
    name="yonnn.discovery.resolve_dns_batch",
    queue="fast",
)
def resolve_dns_batch(
    self,
    payload: dict[str, Any],
    job_id: str | None = None,
    workflow_name: str | None = None,
    workflow_instance_id: str | None = None,
    workflow_step_index: int = 1,
    scan_options: dict[str, Any] | None = None,
    root_target_asset_id: str | None = None,
) -> dict[str, Any]:
    """Resolve saved SUBDOMAIN assets to IP assets in batches (no per-host Celery tasks)."""

    async def work(session: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
        program_id = uuid.UUID(str(payload["program_id"]))
        raw_ids = payload.get("subdomain_asset_ids") or []
        if not raw_ids:
            return {"program_id": str(program_id), "ips_linked": 0, "subdomains_processed": 0}

        id_list = [uuid.UUID(str(x)) for x in raw_ids]
        ips_linked = 0
        subdomains_processed = 0

        for batch_start in range(0, len(id_list), DNS_RESOLVE_BATCH_SIZE):
            batch_ids = id_list[batch_start : batch_start + DNS_RESOLVE_BATCH_SIZE]
            result = await session.execute(select(Asset).where(Asset.id.in_(batch_ids)))
            assets = {a.id: a for a in result.scalars().all()}

            ordered: list[tuple[uuid.UUID, str]] = []
            for aid in batch_ids:
                asset = assets.get(aid)
                if asset is None:
                    logger.warning("resolve_dns_batch: missing asset_id={}", aid)
                    continue
                if asset.type != AssetType.SUBDOMAIN.value:
                    logger.debug(
                        "resolve_dns_batch: skip non-SUBDOMAIN asset_id={} type={}",
                        aid,
                        asset.type,
                    )
                    continue
                if asset.program_id != program_id:
                    logger.warning(
                        "resolve_dns_batch: program mismatch asset_id={}",
                        aid,
                    )
                    continue
                ordered.append((aid, asset.value))

            if not ordered:
                continue

            lookup_tasks = [_resolve_host_ips(host) for _, host in ordered]
            ip_lists: list[list[str]] = await asyncio.gather(*lookup_tasks)

            for (sub_id, hostname), ips in zip(ordered, ip_lists, strict=True):
                subdomains_processed += 1
                if not ips:
                    continue
                for ip in ips:
                    await add_discovered_asset(
                        session,
                        program_id,
                        AssetType.IP.value,
                        ip,
                        parent_id=sub_id,
                        relation_type=RelationType.RESOLVES_TO.value,
                        metadata={"source": "dns_resolve_batch", "hostname": hostname},
                    )
                    ips_linked += 1

        logger.info(
            "resolve_dns_batch task_id={} subdomains_processed={} ips_linked={}",
            self.request.id,
            subdomains_processed,
            ips_linked,
        )
        return {
            "program_id": str(program_id),
            "ips_linked": ips_linked,
            "subdomains_processed": subdomains_processed,
        }

    out = self.run_with_session(work, payload)
    if job_id:
        try:
            jid = uuid.UUID(job_id)
            sync_mark_job_status(jid, JobStatus.COMPLETED.value)
        except Exception:
            logger.exception("resolve_dns_batch: could not mark job completed")
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
    name="yonnn.discovery.process_subdomain_discovery",
    queue="slow",
)
def process_subdomain_discovery(
    self,
    program_id: str,
    root_domain_asset_id: str,
    domain: str,
    job_id: str | None = None,
    attach_dns_chain: bool = True,
    workflow_name: str | None = None,
    workflow_instance_id: str | None = None,
    workflow_step_index: int = 0,
    scan_options: dict[str, Any] | None = None,
    root_target_asset_id: str | None = None,
) -> dict[str, Any]:
    """Run Subfinder, upsert subdomains, link to root DOMAIN; optionally chain or delegate to workflow router."""

    job_uuid: uuid.UUID | None = None
    if job_id:
        try:
            job_uuid = uuid.UUID(job_id)
        except ValueError:
            job_uuid = None
    if job_uuid is not None:
        sync_mark_job_status(job_uuid, JobStatus.RUNNING.value)

    async def work(
        session: AsyncSession,
        program_id: str,
        root_domain_asset_id: str,
        domain: str,
        task_id: str | None,
    ) -> dict[str, Any]:
        program_uuid = uuid.UUID(program_id)
        root_uuid = uuid.UUID(root_domain_asset_id)
        root = await session.get(Asset, root_uuid)
        if root is None:
            msg = f"Root domain asset not found: {root_domain_asset_id}"
            logger.error(msg)
            raise ValueError(msg)
        if root.program_id != program_uuid:
            msg = "Root asset does not belong to the given program"
            logger.error(msg)
            raise ValueError(msg)
        if root.type != AssetType.DOMAIN.value:
            msg = f"Root asset must be DOMAIN, got {root.type!r}"
            logger.error(msg)
            raise ValueError(msg)

        tool = SubfinderTask()
        raw_name = f"subfinder-{task_id}.jsonl" if task_id else None
        _, rows = await tool.run_and_parse(
            ["-d", domain.strip(), "-json"],
            save_raw_filename=raw_name,
        )

        root_norm = root.value.strip().lower().rstrip(".")
        seen_hosts: set[str] = set()
        subdomain_asset_ids: list[str] = []

        for row in rows:
            host = _host_from_row(row)
            if not host or host in seen_hosts:
                continue
            if host == root_norm:
                continue
            seen_hosts.add(host)

            sub = await upsert_asset(
                session,
                program_uuid,
                AssetType.SUBDOMAIN.value,
                host,
                metadata={"source": "subfinder"},
            )
            await link_assets(
                session,
                root.id,
                sub.id,
                RelationType.CONTAINS.value,
            )
            subdomain_asset_ids.append(str(sub.id))

        logger.info(
            "process_subdomain_discovery task_id={} program_id={} new_or_updated_subdomains={}",
            task_id,
            program_id,
            len(subdomain_asset_ids),
        )

        return {
            "program_id": str(program_uuid),
            "subdomain_asset_ids": subdomain_asset_ids,
            "root_domain_asset_id": str(root.id),
            "domain": domain.strip(),
        }

    task_id = getattr(self.request, "id", None)
    try:
        payload = self.run_with_session(
            work,
            program_id,
            root_domain_asset_id,
            domain,
            task_id,
        )

        rta = root_target_asset_id or root_domain_asset_id
        if attach_dns_chain:
            if payload.get("subdomain_asset_ids"):
                chain(resolve_dns_batch.s(payload)).apply_async()
            else:
                logger.info(
                    "process_subdomain_discovery task_id={}: no subdomains to resolve; skip DNS chain",
                    task_id,
                )
        elif workflow_name and workflow_instance_id:
            from services.workflow_service import trigger_next_step

            trigger_next_step(
                payload,
                workflow_name,
                workflow_step_index,
                job_id=job_id,
                workflow_instance_id=workflow_instance_id,
                scan_options=scan_options,
                root_target_asset_id=rta,
            )

        if job_uuid is not None:
            sync_mark_job_status(job_uuid, JobStatus.COMPLETED.value)
        return payload
    except Exception as exc:
        if job_uuid is not None:
            detail = str(exc)[:2000]
            sync_mark_job_status(job_uuid, JobStatus.FAILED.value, error_detail=detail)
        raise
