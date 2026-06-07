"""Dynamic workflow router: tool compatibility, bridging, Celery chains (spec §2)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from models.asset import Asset
from models.enums import AssetType, JobStatus, ToolDatumKind
from models.program import Program
from services import job_service

# ---------------------------------------------------------------------------
# Compatibility: what one step's primary output may feed into the next step
# ---------------------------------------------------------------------------


def _feeds(prev_out: ToolDatumKind, next_in: ToolDatumKind) -> bool:
    if prev_out == next_in:
        return True
    if next_in == ToolDatumKind.ARBITRARY:
        return True
    if prev_out == ToolDatumKind.IP and next_in == ToolDatumKind.HOST_OR_IP:
        return True
    if prev_out == ToolDatumKind.ARBITRARY and next_in == ToolDatumKind.HOST_OR_IP:
        return True
    return False


# When A's output cannot feed B's input, insert these step ids (in order) between them.
AUTO_BRIDGE_STEPS: dict[tuple[ToolDatumKind, ToolDatumKind], tuple[str, ...]] = {
    (ToolDatumKind.SUBDOMAIN, ToolDatumKind.IP): ("dns_resolve",),
    (ToolDatumKind.SUBDOMAIN, ToolDatumKind.HOST_OR_IP): ("dns_resolve",),
}


def _bridge_label(step_ids: tuple[str, ...]) -> str:
    return " / ".join(step_ids) if step_ids else "bridging"


@dataclass(frozen=True)
class WorkflowStep:
    """One executable step: Celery binding + workflow typing + empty-result short-circuit."""

    step_id: str
    celery_task: str
    queue: str
    job_tool_name: str
    primary_input: ToolDatumKind
    primary_output: ToolDatumKind
    requires_asset_types: frozenset[str]
    """If non-empty, the target asset's ``type`` must be one of these for step 0."""
    stop_chain_if: Callable[[dict[str, Any]], bool] | None = None
    execution_hint: str = "batch"
    """``batch`` = one Celery task carries full lists (e.g. 100 subdomain ids in one payload)."""


def _no_subdomains_after_subfinder(result: dict[str, Any]) -> bool:
    ids = result.get("subdomain_asset_ids") or []
    return len(ids) == 0


def _skip_httpx_after_dns(result: dict[str, Any]) -> bool:
    return (result.get("subdomains_processed") or 0) == 0


def _skip_nuclei_after_empty_nmap(result: dict[str, Any]) -> bool:
    r = result.get("results") or []
    return len(r) == 0


WORKFLOW_STEP_BY_ID: dict[str, WorkflowStep] = {
    "subfinder": WorkflowStep(
        step_id="subfinder",
        celery_task="yonnn.discovery.process_subdomain_discovery",
        queue="slow",
        job_tool_name="discovery.subfinder",
        primary_input=ToolDatumKind.DOMAIN,
        primary_output=ToolDatumKind.SUBDOMAIN,
        requires_asset_types=frozenset({AssetType.DOMAIN.value}),
        stop_chain_if=_no_subdomains_after_subfinder,
        execution_hint="batch",
    ),
    "dns_resolve": WorkflowStep(
        step_id="dns_resolve",
        celery_task="yonnn.discovery.resolve_dns_batch",
        queue="fast",
        job_tool_name="discovery.dns_resolve",
        primary_input=ToolDatumKind.SUBDOMAIN,
        primary_output=ToolDatumKind.IP,
        requires_asset_types=frozenset(),
        stop_chain_if=None,
        execution_hint="batch",
    ),
    "httpx_stub": WorkflowStep(
        step_id="httpx_stub",
        celery_task="yonnn.workflow.http_probe_stub",
        queue="slow",
        job_tool_name="discovery.httpx_stub",
        primary_input=ToolDatumKind.IP,
        primary_output=ToolDatumKind.ARBITRARY,
        requires_asset_types=frozenset(),
        stop_chain_if=None,
        execution_hint="batch",
    ),
    "nmap": WorkflowStep(
        step_id="nmap",
        celery_task="yonnn.workflow.tool_stub",
        queue="slow",
        job_tool_name="vuln_scan.nmap",
        primary_input=ToolDatumKind.HOST_OR_IP,
        primary_output=ToolDatumKind.ARBITRARY,
        requires_asset_types=frozenset(),
        stop_chain_if=_skip_nuclei_after_empty_nmap,
        execution_hint="batch",
    ),
    "nuclei": WorkflowStep(
        step_id="nuclei",
        celery_task="yonnn.workflow.tool_stub",
        queue="slow",
        job_tool_name="vuln_scan.nuclei",
        primary_input=ToolDatumKind.HOST_OR_IP,
        primary_output=ToolDatumKind.ARBITRARY,
        requires_asset_types=frozenset(),
        stop_chain_if=None,
        execution_hint="batch",
    ),
}

NAMED_WORKFLOW_SEQUENCES: dict[str, tuple[str, ...]] = {
    "discovery": ("subfinder", "dns_resolve", "httpx_stub"),
    "vuln_scan": ("nmap", "nuclei"),
}

WORKFLOW_REGISTRY: dict[str, tuple[WorkflowStep, ...]] = {
    name: tuple(WORKFLOW_STEP_BY_ID[sid] for sid in seq)
    for name, seq in NAMED_WORKFLOW_SEQUENCES.items()
}

CUSTOM_WORKFLOW_TYPE = "custom"
WORKFLOW_SEQUENCE_OPTIONS_KEY = "_workflow_sequence"


def list_workflow_names() -> list[str]:
    return sorted(NAMED_WORKFLOW_SEQUENCES.keys())


def list_tool_step_ids() -> list[str]:
    return sorted(WORKFLOW_STEP_BY_ID.keys())


def get_workflow_steps(workflow_name: str) -> tuple[WorkflowStep, ...] | None:
    key = workflow_name.strip().lower()
    return WORKFLOW_REGISTRY.get(key)


def steps_from_resolved_ids(resolved_ids: list[str]) -> tuple[WorkflowStep, ...]:
    out: list[WorkflowStep] = []
    for sid in resolved_ids:
        spec = WORKFLOW_STEP_BY_ID.get(sid)
        if spec is None:
            msg = f"Unknown workflow tool {sid!r}; known: {', '.join(list_tool_step_ids())}"
            raise ValueError(msg)
        out.append(spec)
    return tuple(out)


def validate_sequence(
    tool_ids: list[str],
    *,
    auto_bridge: bool = False,
) -> tuple[list[str], list[str]]:
    """Check pairwise tool compatibility.

    Returns ``(resolved_sequence, bridge_steps_inserted)``. Raises ``ValueError`` with a
    descriptive message when the chain is impossible or unrelated without a known bridge.

    With ``auto_bridge=True``, inserts steps from :data:`AUTO_BRIDGE_STEPS` (e.g. DNS between
    Subfinder and Nmap-style IP consumers).
    """
    if not tool_ids:
        raise ValueError("workflow sequence cannot be empty")

    normalized = [t.strip().lower() for t in tool_ids if t and str(t).strip()]
    if len(normalized) != len(tool_ids):
        raise ValueError("workflow sequence contains empty or whitespace-only entries")

    for sid in normalized:
        if sid not in WORKFLOW_STEP_BY_ID:
            raise ValueError(
                f"Unknown tool {sid!r} in sequence; allowed: {', '.join(list_tool_step_ids())}",
            )

    bridge_log: list[str] = []
    resolved: list[str] = [normalized[0]]

    for i in range(len(normalized) - 1):
        b_id = normalized[i + 1]
        a_id = resolved[-1]
        a = WORKFLOW_STEP_BY_ID[a_id]
        b = WORKFLOW_STEP_BY_ID[b_id]
        if _feeds(a.primary_output, b.primary_input):
            resolved.append(b_id)
            continue

        key = (a.primary_output, b.primary_input)
        bridge = AUTO_BRIDGE_STEPS.get(key)
        if bridge is None:
            raise ValueError(
                f"Tool {b_id!r} cannot follow {a_id!r} without a bridging step "
                f"(output is {a.primary_output.value}, next tool expects {b.primary_input.value}).",
            )
        if not auto_bridge:
            hint = _bridge_label(bridge)
            raise ValueError(
                f"Tool {b_id!r} cannot follow {a_id!r} without a {hint} step. "
                f"Insert {list(bridge)} or enable auto-bridging.",
            )
        resolved.extend(bridge)
        bridge_log.extend(bridge)
        resolved.append(b_id)

    return resolved, bridge_log


def suggest_sequence_with_bridges(tool_ids: list[str]) -> tuple[list[str], list[str]]:
    """Preview a valid chain after inserting known bridges (same as ``validate_sequence(..., auto_bridge=True)``)."""
    return validate_sequence(tool_ids, auto_bridge=True)


def validate_sequence_against_target(
    resolved_ids: list[str],
    target_asset: Asset,
) -> None:
    """Ensure step 0 is allowed for the given program asset."""
    if not resolved_ids:
        raise ValueError("empty resolved sequence")
    first = WORKFLOW_STEP_BY_ID[resolved_ids[0]]
    if first.requires_asset_types and target_asset.type not in first.requires_asset_types:
        need = ", ".join(sorted(first.requires_asset_types))
        raise ValueError(
            f"First tool {first.step_id!r} requires target asset type(s) [{need}], "
            f"got {target_asset.type!r}.",
        )


def validate_named_workflow(workflow_name: str) -> None:
    """Sanity-check bundled workflows at load (all pairs compatible)."""
    wkey = workflow_name.strip().lower()
    seq = NAMED_WORKFLOW_SEQUENCES.get(wkey)
    if not seq:
        return
    validate_sequence(list(seq), auto_bridge=False)


for _wf in NAMED_WORKFLOW_SEQUENCES:
    validate_named_workflow(_wf)


def _resolve_steps_for_run(
    workflow_name: str,
    scan_options: dict[str, Any] | None,
) -> tuple[WorkflowStep, ...] | None:
    wkey = workflow_name.strip().lower()
    if wkey == CUSTOM_WORKFLOW_TYPE and scan_options:
        seq = scan_options.get(WORKFLOW_SEQUENCE_OPTIONS_KEY)
        if isinstance(seq, list) and seq:
            return steps_from_resolved_ids([str(x).strip().lower() for x in seq])
    return WORKFLOW_REGISTRY.get(wkey)


def trigger_next_step(
    current_task_result: dict[str, Any],
    workflow_name: str,
    completed_step_index: int,
    *,
    job_id: str | None,
    workflow_instance_id: str,
    scan_options: dict[str, Any] | None,
    root_target_asset_id: str,
) -> dict[str, Any]:
    from workers.celery_app import celery_app

    wname = workflow_name.strip().lower()
    steps = _resolve_steps_for_run(wname, scan_options)
    if not steps:
        logger.warning("trigger_next_step: unknown workflow {!r}", workflow_name)
        return {"dispatched": False, "reason": "unknown_workflow"}

    if completed_step_index < 0 or completed_step_index >= len(steps):
        return {"dispatched": False, "reason": "invalid_step_index"}

    finished = steps[completed_step_index]
    checker = finished.stop_chain_if
    if checker is not None and checker(current_task_result):
        logger.info(
            "workflow {}: stop after step {} ({}) — empty / short-circuit rule",
            wname,
            completed_step_index,
            finished.step_id,
        )
        return {
            "dispatched": False,
            "reason": "stopped_after_empty_result",
            "step_id": finished.step_id,
        }

    next_index = completed_step_index + 1
    if next_index >= len(steps):
        logger.info("workflow {} complete after step {}", wname, finished.step_id)
        return {"dispatched": False, "reason": "workflow_complete"}

    nxt = steps[next_index]
    wf_uuid = uuid.UUID(workflow_instance_id)
    root_uuid = uuid.UUID(root_target_asset_id)
    opts = dict(scan_options) if isinstance(scan_options, dict) else None

    child_job_id = job_service.sync_create_job(
        tool_name=nxt.job_tool_name,
        target_asset_id=root_uuid,
        status=JobStatus.PENDING.value,
        workflow_instance_id=wf_uuid,
        workflow_type=wname,
        workflow_step_index=next_index,
        scan_options=opts,
    )

    args, kwargs = _build_celery_invocation(
        wname,
        next_index,
        nxt,
        current_task_result,
        child_job_id=str(child_job_id),
        workflow_instance_id=workflow_instance_id,
        scan_options=opts,
        root_target_asset_id=root_target_asset_id,
    )

    async_result = celery_app.send_task(
        nxt.celery_task,
        args=args,
        kwargs=kwargs,
        queue=nxt.queue,
    )
    job_service.sync_set_celery_task_id(child_job_id, async_result.id)
    logger.info(
        "workflow {}: dispatched step {} ({}) job_id={} celery_id={}",
        wname,
        next_index,
        nxt.step_id,
        child_job_id,
        async_result.id,
    )
    return {
        "dispatched": True,
        "next_step_index": next_index,
        "next_step_id": nxt.step_id,
        "job_id": str(child_job_id),
        "celery_task_id": async_result.id,
    }


def _build_celery_invocation(
    workflow_name: str,
    next_index: int,
    step: WorkflowStep,
    previous_result: dict[str, Any],
    *,
    child_job_id: str,
    workflow_instance_id: str,
    scan_options: dict[str, Any] | None,
    root_target_asset_id: str,
) -> tuple[list[Any], dict[str, Any]]:
    common_kw: dict[str, Any] = {
        "job_id": child_job_id,
        "workflow_name": workflow_name,
        "workflow_instance_id": workflow_instance_id,
        "workflow_step_index": next_index,
        "scan_options": scan_options,
        "root_target_asset_id": root_target_asset_id,
    }

    if step.step_id == "dns_resolve":
        return [], {"payload": previous_result, **common_kw}
    if step.step_id == "httpx_stub":
        pid = str(previous_result.get("program_id") or "")
        return [], {"program_id": pid, **common_kw}
    if step.celery_task == "yonnn.workflow.tool_stub":
        return (
            [],
            {
                "previous": previous_result,
                "step_label": step.step_id,
                **common_kw,
            },
        )

    logger.error("No Celery arg builder for workflow={} step={}", workflow_name, step.step_id)
    return [], common_kw


async def start_workflow(
    session: AsyncSession,
    *,
    workflow_name: str,
    program: Program,
    target_asset: Asset,
    scan_options: dict[str, Any],
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Start a named bundled workflow (sequence defined in :data:`NAMED_WORKFLOW_SEQUENCES`)."""
    from workers.celery_app import celery_app

    wkey = workflow_name.strip().lower()
    seq = NAMED_WORKFLOW_SEQUENCES.get(wkey)
    if not seq:
        allowed = ", ".join(list_workflow_names())
        msg = f"Unknown workflow_type {workflow_name!r}; allowed: {allowed}"
        raise ValueError(msg)

    resolved = list(seq)
    validate_sequence_against_target(resolved, target_asset)
    validate_sequence(resolved, auto_bridge=False)

    steps = WORKFLOW_REGISTRY[wkey]
    wf_instance_id = uuid.uuid4()
    first = steps[0]

    primary_job = await job_service.create_job(
        session,
        tool_name=first.job_tool_name,
        target_asset_id=target_asset.id,
        status=JobStatus.PENDING.value,
        workflow_instance_id=wf_instance_id,
        workflow_type=wkey,
        workflow_step_index=0,
        scan_options=scan_options,
    )
    await session.flush()

    common_kw: dict[str, Any] = {
        "job_id": str(primary_job.id),
        "workflow_name": wkey,
        "workflow_instance_id": str(wf_instance_id),
        "workflow_step_index": 0,
        "scan_options": scan_options,
        "root_target_asset_id": str(target_asset.id),
    }

    if wkey == "discovery":
        domain = (target_asset.value or "").strip()
        if not domain:
            msg = "DOMAIN asset has empty value"
            raise ValueError(msg)
        async_result = celery_app.send_task(
            first.celery_task,
            args=[str(program.id), str(target_asset.id), domain],
            kwargs={**common_kw, "attach_dns_chain": False},
            queue=first.queue,
        )
    elif wkey == "vuln_scan":
        async_result = celery_app.send_task(
            first.celery_task,
            args=[],
            kwargs={**common_kw, "previous": {}, "step_label": first.step_id},
            queue=first.queue,
        )
    else:
        msg = f"Workflow {wkey!r} is not wired in start_workflow"
        raise ValueError(msg)

    await job_service.set_celery_task_id(session, primary_job.id, async_result.id)
    return primary_job.id, wf_instance_id, async_result.id


async def start_custom_workflow(
    session: AsyncSession,
    *,
    program: Program,
    target_asset: Asset,
    scan_options: dict[str, Any],
    tool_sequence: list[str],
    auto_bridge: bool,
) -> tuple[uuid.UUID, uuid.UUID, str, list[str], list[str]]:
    """Validate + optional bridge, persist resolved sequence in ``scan_options``, enqueue step 0.

    Returns ``(job_id, workflow_instance_id, celery_task_id, resolved_sequence, bridge_steps_inserted)``.
    """
    from workers.celery_app import celery_app

    resolved, bridge_log = validate_sequence(tool_sequence, auto_bridge=auto_bridge)
    validate_sequence_against_target(resolved, target_asset)

    opts = dict(scan_options)
    opts[WORKFLOW_SEQUENCE_OPTIONS_KEY] = resolved

    steps = steps_from_resolved_ids(resolved)
    wf_instance_id = uuid.uuid4()
    first = steps[0]

    primary_job = await job_service.create_job(
        session,
        tool_name=first.job_tool_name,
        target_asset_id=target_asset.id,
        status=JobStatus.PENDING.value,
        workflow_instance_id=wf_instance_id,
        workflow_type=CUSTOM_WORKFLOW_TYPE,
        workflow_step_index=0,
        scan_options=opts,
    )
    await session.flush()

    common_kw: dict[str, Any] = {
        "job_id": str(primary_job.id),
        "workflow_name": CUSTOM_WORKFLOW_TYPE,
        "workflow_instance_id": str(wf_instance_id),
        "workflow_step_index": 0,
        "scan_options": opts,
        "root_target_asset_id": str(target_asset.id),
    }

    if first.step_id == "subfinder":
        if target_asset.type != AssetType.DOMAIN.value:
            msg = "subfinder requires a DOMAIN target asset"
            raise ValueError(msg)
        domain = (target_asset.value or "").strip()
        if not domain:
            msg = "DOMAIN asset has empty value"
            raise ValueError(msg)
        # Internal DNS chain only when no explicit dns_resolve step follows in the resolved plan.
        attach_dns_chain = not (len(resolved) > 1 and resolved[1] == "dns_resolve")
        async_result = celery_app.send_task(
            first.celery_task,
            args=[str(program.id), str(target_asset.id), domain],
            kwargs={**common_kw, "attach_dns_chain": attach_dns_chain},
            queue=first.queue,
        )
    elif first.celery_task == "yonnn.workflow.tool_stub":
        async_result = celery_app.send_task(
            first.celery_task,
            args=[],
            kwargs={**common_kw, "previous": {}, "step_label": first.step_id},
            queue=first.queue,
        )
    else:
        msg = f"First tool {first.step_id!r} is not wired for custom workflow start"
        raise ValueError(msg)

    await job_service.set_celery_task_id(session, primary_job.id, async_result.id)
    return primary_job.id, wf_instance_id, async_result.id, resolved, bridge_log
