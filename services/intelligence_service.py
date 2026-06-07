"""Intelligence engine: correlate tool outputs into shared infra, tech stack, cloud tags, and findings."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from models.asset import Asset
from models.asset_relation import AssetRelation
from models.enums import AssetType, FindingSeverity, RelationType
from models.finding import Finding
from services import asset_service

# --- Correlation thresholds -------------------------------------------------

# Spec wording: "more than 5 subdomains" → count must be strictly greater than 5.
DEFAULT_HOT_IP_SUBDOMAIN_THRESHOLD = 5

INTEL_KEY = "intelligence"
TOOL_SOURCE_INTEL = "intelligence_engine"

# --- Modular scan settings (passive vs active) ----------------------------

# Passive: metadata correlation (shared IPs, tech fingerprints).
# Active: vulnerability-style pattern checks (takeover, cloud ASN tagging noise).
DEFAULT_INTELLIGENCE_SCAN_SETTINGS: dict[str, bool] = {
    "correlate_shared_ips": True,
    "fingerprint_technologies": True,
    "check_takeover": True,
    "tag_cloud_providers": True,
}


def default_program_intelligence_settings() -> dict[str, bool]:
    """Defaults merged into ``Program.settings['intelligence']`` on create."""
    return dict(DEFAULT_INTELLIGENCE_SCAN_SETTINGS)


def merge_scan_options_for_job(
    program_settings: dict[str, Any] | None,
    request_options: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge program policy with per-launch ``options`` (flat keys and nested ``intelligence``)."""
    intel = default_program_intelligence_settings()
    ps = program_settings if isinstance(program_settings, dict) else {}
    nested = ps.get("intelligence")
    if isinstance(nested, dict):
        for k in intel:
            if k in nested:
                intel[k] = bool(nested[k])
    if request_options:
        ri = request_options.get("intelligence")
        if isinstance(ri, dict):
            for k in intel:
                if k in ri:
                    intel[k] = bool(ri[k])
        for k in intel:
            if k in request_options:
                intel[k] = bool(request_options[k])
    out: dict[str, Any] = {"intelligence": intel}
    for k, v in (request_options or {}).items():
        if k != "intelligence" and k not in intel:
            out[k] = v
    return out


def resolve_intelligence_scan_settings(scan_settings: dict[str, Any] | None) -> dict[str, bool]:
    """Normalize to a bool map for feature gates."""
    base = default_program_intelligence_settings()
    if not scan_settings:
        return base
    block = scan_settings.get("intelligence")
    src = block if isinstance(block, dict) else scan_settings
    if not isinstance(src, dict):
        return base
    for k in base:
        if k in src:
            base[k] = bool(src[k])
    return base


# CNAME targets that often indicate dangling third-party hosting (takeover risk).
_TAKEOVER_CNAME_PATTERNS: tuple[str, ...] = (
    ".s3.amazonaws.com",
    ".s3.dualstack.",
    ".s3-accesspoint.",
    ".herokuapp.com",
    ".github.io",
    ".azurewebsites.net",
    ".cloudapp.azure.com",
    ".trafficmanager.net",
    ".elb.amazonaws.com",
    ".cloudfront.net",
    ".fastly.net",
    ".netlify.app",
    ".vercel.app",
    ".pantheonsite.io",
    ".shopify.com",
    ".myshopify.com",
    ".ghost.io",
    ".readme.io",
    ".surge.sh",
    ".bitbucket.io",
)

# HTTP / body hints that the third-party resource is unclaimed.
_TAKEOVER_BODY_MARKERS: tuple[str, ...] = (
    "nosuchbucket",
    "the specified bucket does not exist",
    "there isn't a github pages site here",
    "no such app",
    "there is no app configured at this hostname",
    "fastly error: unknown domain",
    "project not found",
)

_CLOUD_VENDOR_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "aws",
        (
            "amazon",
            "amazon.com",
            "aws",
            "ec2",
            "amazon data services",
            "aws ec2",
        ),
    ),
    (
        "gcp",
        (
            "google llc",
            "google cloud",
            "gcp",
            "google fiber",
        ),
    ),
    (
        "azure",
        (
            "microsoft corporation",
            "microsoft azure",
            "azure",
        ),
    ),
    (
        "oracle",
        (
            "oracle cloud",
            "oci",
        ),
    ),
    (
        "digitalocean",
        (
            "digitalocean",
            "do networks",
        ),
    ),
)


def compute_finding_dedupe_hash(asset_id: uuid.UUID, title: str, tool_source: str) -> str:
    """SHA-256 hex digest of ``asset_id|title|tool_source`` (spec §2 dedupe)."""
    raw = f"{asset_id}|{title.strip()}|{tool_source.strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def create_finding(
    session: AsyncSession,
    *,
    asset_id: uuid.UUID,
    tool_source: str,
    severity: str,
    title: str,
    description: str | None = None,
    raw_output_link: str | None = None,
    vulnerability_type: str = "correlation",
    endpoint: str | None = None,
) -> tuple[Finding, bool]:
    """Insert or touch a finding by ``dedupe_hash``; returns ``(row, created_new)``."""
    title_store = title.strip()[:512]
    if not title_store:
        msg = "Finding title cannot be empty"
        raise ValueError(msg)
    h = compute_finding_dedupe_hash(asset_id, title_store, tool_source)
    stmt = select(Finding).where(Finding.dedupe_hash == h)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()
    now = datetime.now(UTC)
    if existing is not None:
        existing.last_seen = now
        if description is not None:
            existing.description = description
        if endpoint is not None:
            existing.endpoint = endpoint
        await session.flush()
        return existing, False

    row = Finding(
        asset_id=asset_id,
        tool_source=tool_source.strip()[:128] or TOOL_SOURCE_INTEL,
        severity=severity.strip().lower()[:32],
        title=title_store,
        description=description,
        raw_output_link=raw_output_link,
        dedupe_hash=h,
        vulnerability_type=(vulnerability_type or "correlation").strip()[:255],
        endpoint=endpoint[:2048] if endpoint else None,
        last_seen=now,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row, True


def _merge_intelligence_block(meta: dict[str, Any] | None, patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(meta) if meta else {}
    intel = dict(out.get(INTEL_KEY) or {})
    for k, v in patch.items():
        if v is None:
            continue
        intel[k] = v
    out[INTEL_KEY] = intel
    return out


async def map_ip_neighborhoods(
    session: AsyncSession,
    *,
    program_id: uuid.UUID | None = None,
    subdomain_count_gt: int = DEFAULT_HOT_IP_SUBDOMAIN_THRESHOLD,
    scan_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Find IPs with ``> subdomain_count_gt`` distinct resolving subdomains; flag those subs as shared infra.

    Uses ``resolves_to`` edges (parent SUBDOMAIN → child IP). Merges ``metadata.intelligence`` on each subdomain.
    """
    ss = resolve_intelligence_scan_settings(scan_settings)
    if not ss.get("correlate_shared_ips", True):
        return {
            "skipped": True,
            "reason": "correlate_shared_ips disabled",
            "hot_ip_count": 0,
            "subdomains_flagged": 0,
        }

    sub = aliased(Asset)
    ip = aliased(Asset)
    cnt = func.count(func.distinct(AssetRelation.parent_id)).label("sub_cnt")

    stmt = (
        select(AssetRelation.child_id, cnt)
        .join(sub, sub.id == AssetRelation.parent_id)
        .join(ip, ip.id == AssetRelation.child_id)
        .where(
            AssetRelation.relation_type == RelationType.RESOLVES_TO.value,
            sub.type == AssetType.SUBDOMAIN.value,
            ip.type == AssetType.IP.value,
        )
        .group_by(AssetRelation.child_id)
        .having(cnt > subdomain_count_gt)
    )
    if program_id is not None:
        stmt = stmt.where(sub.program_id == program_id, ip.program_id == program_id)

    hot_rows = list((await session.execute(stmt)).all())
    hot_ip_ids = [row[0] for row in hot_rows]
    counts_by_ip = {row[0]: int(row[1]) for row in hot_rows}

    subdomains_flagged = 0
    for ip_id in hot_ip_ids:
        ip_asset = await session.get(Asset, ip_id)
        if ip_asset is None:
            continue
        n = counts_by_ip.get(ip_id, 0)
        sub_stmt = select(Asset).join(
            AssetRelation,
            AssetRelation.parent_id == Asset.id,
        ).where(
            AssetRelation.child_id == ip_id,
            AssetRelation.relation_type == RelationType.RESOLVES_TO.value,
            Asset.type == AssetType.SUBDOMAIN.value,
        )
        if program_id is not None:
            sub_stmt = sub_stmt.where(Asset.program_id == program_id)

        subs = list((await session.execute(sub_stmt)).scalars().unique().all())
        for sub_a in subs:
            sub_a.metadata_ = _merge_intelligence_block(
                sub_a.metadata_,
                {
                    "shared_infrastructure": True,
                    "shared_ip_asset_id": str(ip_id),
                    "shared_ip_value": ip_asset.value,
                    "shared_ip_subdomain_count": n,
                    "correlation": "hot_ip_neighborhood",
                },
            )
            sub_a.last_seen = datetime.now(UTC)
            subdomains_flagged += 1

    await session.flush()
    return {
        "hot_ip_count": len(hot_ip_ids),
        "hot_ip_ids": [str(x) for x in hot_ip_ids],
        "subdomains_flagged": subdomains_flagged,
        "threshold": subdomain_count_gt,
    }


def _norm_tech_entry(obj: Any) -> str | None:
    if isinstance(obj, str):
        s = obj.strip()
        return s if s else None
    if isinstance(obj, dict):
        name = (obj.get("name") or obj.get("app") or obj.get("product") or "").strip()
        ver = obj.get("version") or obj.get("versions")
        if isinstance(ver, list) and ver:
            ver = ver[0]
        ver_s = str(ver).strip() if ver else ""
        if not name:
            return None
        return f"{name} {ver_s}".strip() if ver_s else name
    return None


def _extract_technology_strings(metadata: dict[str, Any]) -> list[str]:
    """Pull human-readable tech labels from httpx / Wappalyzer-style JSONB blobs."""
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: str | None) -> None:
        if not raw:
            return
        key = raw.casefold()
        if key not in seen:
            seen.add(key)
            out.append(raw)

    meta = metadata or {}

    for key in (
        "technologies",
        "tech",
        "web_technologies",
        "wappalyzer",
        "fingerprints",
        "knowledgebase",
    ):
        block = meta.get(key)
        if isinstance(block, list):
            for item in block:
                add(_norm_tech_entry(item))
        elif isinstance(block, dict):
            for item in block.values():
                if isinstance(item, list):
                    for x in item:
                        add(_norm_tech_entry(x))
                else:
                    add(_norm_tech_entry(item))

    nested = meta.get("httpx")
    if isinstance(nested, dict):
        for key in ("technologies", "tech", "webserver"):
            block = nested.get(key)
            if isinstance(block, list):
                for item in block:
                    add(_norm_tech_entry(item))
            elif isinstance(block, str):
                for part in re.split(r"[,;]", block):
                    add(part.strip())

    for ws_key in ("webserver", "server", "http_server", "product"):
        val = meta.get(ws_key)
        if isinstance(val, str) and val.strip():
            add(val.strip())

    return out


async def _ports_for_tech_source(session: AsyncSession, asset: Asset) -> list[Asset]:
    """Resolve PORT assets to attach SERVICE children (URL hosts PORT; PORT is itself)."""
    if asset.type == AssetType.PORT.value:
        return [asset]
    if asset.type != AssetType.URL.value:
        return []
    stmt = select(Asset).join(
        AssetRelation,
        (AssetRelation.child_id == Asset.id) & (AssetRelation.parent_id == asset.id),
    ).where(
        AssetRelation.relation_type == RelationType.HOSTS.value,
        Asset.type == AssetType.PORT.value,
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return rows


async def sync_technology_fingerprints(
    session: AsyncSession,
    *,
    program_id: uuid.UUID | None = None,
    asset_ids: list[uuid.UUID] | None = None,
    scan_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Parse httpx / Wappalyzer-like metadata on URL and PORT assets; upsert SERVICE + ``runs_on`` links."""
    ss = resolve_intelligence_scan_settings(scan_settings)
    if not ss.get("fingerprint_technologies", True):
        return {
            "skipped": True,
            "reason": "fingerprint_technologies disabled",
            "assets_scanned": 0,
            "fingerprint_operations": 0,
        }

    stmt = select(Asset).where(
        Asset.type.in_((AssetType.URL.value, AssetType.PORT.value)),
    )
    if program_id is not None:
        stmt = stmt.where(Asset.program_id == program_id)
    if asset_ids is not None:
        if not asset_ids:
            return {"assets_scanned": 0, "fingerprint_operations": 0}
        stmt = stmt.where(Asset.id.in_(asset_ids))

    assets = list((await session.execute(stmt)).scalars().all())
    fingerprint_ops = 0

    for a in assets:
        techs = _extract_technology_strings(dict(a.metadata_ or {}))
        if not techs:
            continue
        ports = await _ports_for_tech_source(session, a)
        if not ports:
            continue
        for label in techs:
            val = label.strip()[:2048]
            if not val:
                continue
            for port in ports:
                svc = await asset_service.upsert_asset(
                    session,
                    port.program_id,
                    AssetType.SERVICE.value,
                    val,
                    metadata={
                        INTEL_KEY: {
                            "source": "technology_fingerprint",
                            "observed_from_asset_id": str(a.id),
                            "observed_from_type": a.type,
                        },
                    },
                )
                await asset_service.link_assets(
                    session,
                    port.id,
                    svc.id,
                    RelationType.RUNS_ON.value,
                )
                fingerprint_ops += 1

    await session.flush()
    return {
        "assets_scanned": len(assets),
        "fingerprint_operations": fingerprint_ops,
    }


def _collect_cname_targets(metadata: dict[str, Any]) -> list[str]:
    """Normalize CNAME strings from common scanner shapes."""
    targets: list[str] = []
    meta = metadata or {}

    def add(s: str | None) -> None:
        if s and isinstance(s, str):
            t = s.strip().lower().rstrip(".")
            if t:
                targets.append(t)

    raw = meta.get("cname") or meta.get("cnames")
    if isinstance(raw, str):
        add(raw)
    elif isinstance(raw, list):
        for x in raw:
            if isinstance(x, str):
                add(x)
            elif isinstance(x, dict):
                add(x.get("host") or x.get("target") or x.get("value"))

    dns = meta.get("dns") or meta.get("dns_records")
    if isinstance(dns, dict):
        recs = dns.get("CNAME") or dns.get("cname")
        if isinstance(recs, list):
            for x in recs:
                if isinstance(x, str):
                    add(x)
                elif isinstance(x, dict):
                    add(x.get("host") or x.get("target") or x.get("value"))
        elif isinstance(recs, str):
            add(recs)

    nested = meta.get("httpx") or meta.get("probe")
    if isinstance(nested, dict):
        for key in ("cname", "cnames", "chain"):
            v = nested.get(key)
            if isinstance(v, str):
                add(v)
            elif isinstance(v, list) and v and isinstance(v[0], str):
                for x in v:
                    add(x)

    return targets


def _cname_matches_takeover_pattern(cname: str) -> bool:
    c = cname.lower().rstrip(".")
    return any(pat in c for pat in _TAKEOVER_CNAME_PATTERNS)


def _http_indicates_unclaimed(metadata: dict[str, Any]) -> bool:
    """True when stored probe data looks like a missing third-party bucket/app page."""
    meta = metadata or {}
    blocks: list[dict[str, Any]] = []
    if isinstance(meta.get("httpx"), dict):
        blocks.append(meta["httpx"])
    if isinstance(meta.get("probe"), dict):
        blocks.append(meta["probe"])
    blocks.append(meta)

    status: int | None = None
    body_l = ""
    for b in blocks:
        sc = b.get("status_code") or b.get("status-code") or b.get("http_status")
        if isinstance(sc, int):
            status = sc
        elif isinstance(sc, str) and sc.isdigit():
            status = int(sc)
        for bk in ("body", "response", "raw", "lines"):
            chunk = b.get(bk)
            if isinstance(chunk, str):
                body_l += chunk.casefold()
            elif isinstance(chunk, list):
                body_l += " ".join(str(x) for x in chunk).casefold()

    if status == 404:
        return True
    for marker in _TAKEOVER_BODY_MARKERS:
        if marker.casefold() in body_l:
            return True
    return False


async def scan_subdomain_takeover_signals(
    session: AsyncSession,
    *,
    program_id: uuid.UUID | None = None,
    scan_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Flag likely dangling CNAMEs (S3/Heroku/GitHub Pages patterns) with error-like HTTP metadata."""
    ss = resolve_intelligence_scan_settings(scan_settings)
    if not ss.get("check_takeover", True):
        return {
            "skipped": True,
            "reason": "check_takeover disabled",
            "subdomains_evaluated": 0,
            "findings_new": 0,
            "findings_updated": 0,
        }

    stmt = select(Asset).where(Asset.type == AssetType.SUBDOMAIN.value)
    if program_id is not None:
        stmt = stmt.where(Asset.program_id == program_id)

    subs = list((await session.execute(stmt)).scalars().all())
    findings_created = 0
    findings_touched = 0

    for subdomain_asset in subs:
        meta = dict(subdomain_asset.metadata_ or {})
        cnames = _collect_cname_targets(meta)
        if not cnames:
            continue
        if not any(_cname_matches_takeover_pattern(c) for c in cnames):
            continue
        if not _http_indicates_unclaimed(meta):
            continue

        cname_s = ", ".join(cnames[:6])
        title = f"Possible subdomain takeover (dangling CNAME): {subdomain_asset.value[:200]}"
        desc = (
            f"CNAME chain includes a third-party pattern ({cname_s}) while HTTP response suggests "
            "the resource is unclaimed (e.g. 404 / bucket error). Verify manually before reporting."
        )
        _finding, created_new = await create_finding(
            session,
            asset_id=subdomain_asset.id,
            tool_source=TOOL_SOURCE_INTEL,
            severity=FindingSeverity.HIGH.value,
            title=title,
            description=desc,
            vulnerability_type="subdomain_takeover",
            endpoint=subdomain_asset.value[:2048],
        )
        if created_new:
            findings_created += 1
        else:
            findings_touched += 1

    await session.flush()
    return {
        "subdomains_evaluated": len(subs),
        "findings_new": findings_created,
        "findings_updated": findings_touched,
    }


def _asn_blob(metadata: dict[str, Any]) -> str:
    parts: list[str] = []
    for b in (metadata, metadata.get("httpx") if isinstance(metadata.get("httpx"), dict) else {}):
        if not isinstance(b, dict):
            continue
        for k in ("asn", "as_name", "asname", "org", "organization", "isp", "description"):
            v = b.get(k)
            if v is not None:
                parts.append(str(v))
    return " ".join(parts).casefold()


async def tag_cloud_hosted_assets(
    session: AsyncSession,
    *,
    program_id: uuid.UUID | None = None,
    scan_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark IP (and SUBDOMAIN) assets as cloud-hosted when ASN / org strings match known providers."""
    ss = resolve_intelligence_scan_settings(scan_settings)
    if not ss.get("tag_cloud_providers", True):
        return {
            "skipped": True,
            "reason": "tag_cloud_providers disabled",
            "assets_evaluated": 0,
            "assets_tagged_cloud": 0,
        }

    stmt = select(Asset).where(Asset.type.in_((AssetType.IP.value, AssetType.SUBDOMAIN.value)))
    if program_id is not None:
        stmt = stmt.where(Asset.program_id == program_id)

    rows = list((await session.execute(stmt)).scalars().all())
    tagged = 0

    for asset in rows:
        blob = _asn_blob(dict(asset.metadata_ or {}))
        if len(blob) < 4:
            continue
        provider: str | None = None
        for name, needles in _CLOUD_VENDOR_RULES:
            if any(n in blob for n in needles):
                provider = name
                break
        if provider is None:
            continue
        asset.metadata_ = _merge_intelligence_block(
            asset.metadata_,
            {
                "hosting": "cloud-hosted",
                "cloud_provider": provider,
                "correlation": "asn_cloud_mapping",
            },
        )
        asset.last_seen = datetime.now(UTC)
        tagged += 1

    await session.flush()
    return {"assets_evaluated": len(rows), "assets_tagged_cloud": tagged}


async def run_intelligence_pass(
    session: AsyncSession,
    *,
    program_id: uuid.UUID | None = None,
    scan_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run passive modules first, then active modules, honoring ``scan_settings`` (commit by caller)."""
    out: dict[str, Any] = {}
    out["ip_neighborhoods"] = await map_ip_neighborhoods(
        session,
        program_id=program_id,
        scan_settings=scan_settings,
    )
    out["technology_fingerprints"] = await sync_technology_fingerprints(
        session,
        program_id=program_id,
        scan_settings=scan_settings,
    )
    out["subdomain_takeover"] = await scan_subdomain_takeover_signals(
        session,
        program_id=program_id,
        scan_settings=scan_settings,
    )
    out["cloud_tags"] = await tag_cloud_hosted_assets(
        session,
        program_id=program_id,
        scan_settings=scan_settings,
    )
    logger.info("run_intelligence_pass program_id={} summary={}", program_id, {k: v for k, v in out.items()})
    return out
