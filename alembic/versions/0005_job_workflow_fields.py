"""Job workflow tracking: instance id, type, step index, scan options JSONB."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_job_workflow_fields"
down_revision: Union[str, Sequence[str], None] = "0004_job_celery_task_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("workflow_instance_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("jobs", sa.Column("workflow_type", sa.String(length=64), nullable=True))
    op.add_column("jobs", sa.Column("workflow_step_index", sa.Integer(), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("scan_options", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(
        op.f("ix_jobs_workflow_instance_id"),
        "jobs",
        ["workflow_instance_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_jobs_workflow_instance_id"), table_name="jobs")
    op.drop_column("jobs", "scan_options")
    op.drop_column("jobs", "workflow_step_index")
    op.drop_column("jobs", "workflow_type")
    op.drop_column("jobs", "workflow_instance_id")
