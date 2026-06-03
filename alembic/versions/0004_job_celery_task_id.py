"""Store Celery task id on jobs for scan status correlation."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_job_celery_task_id"
down_revision: Union[str, Sequence[str], None] = "0003_program_owner"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("celery_task_id", sa.String(length=256), nullable=True),
    )
    op.create_index(op.f("ix_jobs_celery_task_id"), "jobs", ["celery_task_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_jobs_celery_task_id"), table_name="jobs")
    op.drop_column("jobs", "celery_task_id")
