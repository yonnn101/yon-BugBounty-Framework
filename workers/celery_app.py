"""Execution layer: Celery app — Redis broker/result backend, JSON, queues fast/slow (spec §2, §4)."""

from __future__ import annotations

import os
import sys

from celery import Celery
from celery.signals import worker_process_init
from kombu import Exchange, Queue
from loguru import logger

# ---------------------------------------------------------------------------
# Loguru in worker processes (tasks already use logger; this unifies worker logs)
# ---------------------------------------------------------------------------


@worker_process_init.connect
def _configure_loguru(**_kwargs: object) -> None:
    level = os.environ.get("LOGURU_LEVEL", "INFO")
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
    )


broker_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "yonnn",
    broker=broker_url,
    backend=broker_url,
)

from workers.base_task import YonnnTask  # noqa: E402

celery_app.Task = YonnnTask

default_exchange = Exchange("yonnn", type="direct")

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_extended=True,
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=int(os.environ.get("CELERY_PREFETCH_MULTIPLIER", "1")),
    task_default_queue="slow",
    task_queues=(
        Queue("fast", exchange=default_exchange, routing_key="fast"),
        Queue("slow", exchange=default_exchange, routing_key="slow"),
    ),
    task_routes={
        "yonnn.debug.ping": {"queue": "fast", "routing_key": "fast"},
        "yonnn.discovery.process_subdomain_discovery": {"queue": "slow", "routing_key": "slow"},
        "yonnn.discovery.resolve_dns_batch": {"queue": "fast", "routing_key": "fast"},
        "yonnn.workflow.http_probe_stub": {"queue": "slow", "routing_key": "slow"},
        "yonnn.workflow.tool_stub": {"queue": "slow", "routing_key": "slow"},
    },
    # Ensures worker/call clients load the same task modules as ``import workers.tasks``.
    include=("workers.tasks.debug", "workers.tasks.discovery", "workers.tasks.workflow_runner"),
)

# Register tasks after Task base is bound (package __init__ imports all task modules).
import workers.tasks  # noqa: E402, F401
