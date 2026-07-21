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
        sa.Column("integer_value", sa.Integer(), nullable=True),
        sa.Column("text_value", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "(integer_value IS NOT NULL AND text_value IS NULL) OR "
            "(integer_value IS NULL AND text_value IS NOT NULL)",
            name=op.f("ck_auth_state_exactly_one_value"),
        ),
        sa.CheckConstraint(
            "integer_value IS NULL OR integer_value >= 0",
            name=op.f("ck_auth_state_non_negative_integer"),
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_auth_state")),
    )
    table = sa.table(
        "auth_state",
        sa.column("key", sa.String(length=50)),
        sa.column("integer_value", sa.Integer()),
        sa.column("text_value", sa.String(length=128)),
    )
    op.bulk_insert(
        table,
        [{"key": "session_epoch", "integer_value": 0, "text_value": None}],
    )


def downgrade() -> None:
    """Remove authentication revocation state."""

    op.drop_table("auth_state")
