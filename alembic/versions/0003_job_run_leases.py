"""add worker leases to durable job runs

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable ownership state; pre-existing running rows are stale."""

    with op.batch_alter_table("job_runs") as batch_op:
        batch_op.add_column(sa.Column("worker_id", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("claim_token", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("retry_not_before", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            "job_running_lease",
            ["status", "lease_expires_at"],
            unique=False,
        )


def downgrade() -> None:
    """Remove worker ownership state."""

    with op.batch_alter_table("job_runs") as batch_op:
        batch_op.drop_index("job_running_lease")
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("retry_not_before")
        batch_op.drop_column("claim_token")
        batch_op.drop_column("worker_id")
