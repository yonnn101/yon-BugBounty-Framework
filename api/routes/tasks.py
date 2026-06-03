"""Interface layer: Celery task status for UI polling."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from api.deps import get_current_active_user
from models.user import User
from schemas.celery_task import CeleryTaskStatus

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(i) for i in value]
    return str(value)


@router.get("/{task_id}", response_model=CeleryTaskStatus)
async def get_celery_task_status(
    task_id: str,
    _user: User = Depends(get_current_active_user),
) -> CeleryTaskStatus:
    """Return Redis result-backend state for a task id (e.g. after subdomain discovery)."""
    from workers.celery_app import celery_app

    async_result = celery_app.AsyncResult(task_id)
    state = async_result.state
    result: Any = None
    error: str | None = None

    if state == "SUCCESS":
        result = _jsonable(async_result.result)
    elif state == "FAILURE":
        try:
            err_val = async_result.result
            error = str(err_val) if err_val is not None else "Task failed"
        except Exception:
            error = "Task failed"

    return CeleryTaskStatus(task_id=task_id, state=state, result=result, error=error)
