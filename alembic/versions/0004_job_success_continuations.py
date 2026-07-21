"""add durable successful-job continuation acknowledgement

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Track successful rows whose registered continuations completed."""

    with op.batch_alter_table("job_runs") as batch_op:
        batch_op.add_column(
            sa.Column("continuation_completed_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    """Remove successful-continuation acknowledgement state."""

    with op.batch_alter_table("job_runs") as batch_op:
        batch_op.drop_column("continuation_completed_at")
