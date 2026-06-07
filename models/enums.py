"""Data blueprint: shared string enums stored in PostgreSQL as VARCHAR (no native PG enum)."""

from __future__ import annotations

from enum import StrEnum


class AssetType(StrEnum):
    """Normalized asset kinds for the graph (spec section 1)."""

    DOMAIN = "DOMAIN"
    SUBDOMAIN = "SUBDOMAIN"
    IP = "IP"
    URL = "URL"
    PORT = "PORT"
    SERVICE = "SERVICE"


class RelationType(StrEnum):
    """Edge labels between assets (spec section 1)."""

    RESOLVES_TO = "resolves_to"
    HOSTS = "hosts"
    RUNS_ON = "runs_on"
    CONTAINS = "contains"


class JobStatus(StrEnum):
    """Scan / job lifecycle (spec section 1)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FindingSeverity(StrEnum):
    """Finding severity for notifications and filtering (spec section 5)."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ProgramPlatform(StrEnum):
    """Bug bounty platform identifier."""

    H1 = "H1"
    BC = "BC"


class ToolDatumKind(StrEnum):
    """Primary semantic payload passed between chained tools (workflow compatibility).

    Distinct from :class:`AssetType` when the step moves *batches* or graph-derived context
    rather than a single asset row.
    """

    DOMAIN = "DOMAIN"
    SUBDOMAIN = "SUBDOMAIN"
    IP = "IP"
    HOST_OR_IP = "HOST_OR_IP"
    ARBITRARY = "ARBITRARY"
