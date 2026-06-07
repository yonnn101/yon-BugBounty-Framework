"""Celery task modules — package import registers all tasks with ``celery_app``."""

from . import debug  # noqa: F401
from . import discovery  # noqa: F401
from . import workflow_runner  # noqa: F401
