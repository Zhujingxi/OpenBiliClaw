"""Persist the non-secret authentication revocation epoch.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create and seed the monotonic, non-secret session epoch."""

    op.create_table(
        "auth_state",
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.CheckConstraint("value >= 0", name="ck_auth_state_non_negative"),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_auth_state")),
    )
    table = sa.table(
        "auth_state",
        sa.column("key", sa.String(length=50)),
        sa.column("value", sa.Integer()),
    )
    op.bulk_insert(table, [{"key": "session_epoch", "value": 0}])


def downgrade() -> None:
    """Remove authentication revocation state."""

    op.drop_table("auth_state")
