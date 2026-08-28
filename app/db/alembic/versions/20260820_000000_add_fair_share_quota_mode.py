"""add fair share quota mode setting

Revision ID: 20260820_000000_add_fair_share_quota_mode
Revises: 20260816_000000_add_model_source_embeddings
Create Date: 2026-08-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260820_000000_add_fair_share_quota_mode"
down_revision = "20260816_000000_add_model_source_embeddings"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    dashboard_columns = _columns(bind, "dashboard_settings")
    if not dashboard_columns:
        return
    if "fair_share_quota_mode_enabled" not in dashboard_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "fair_share_quota_mode_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    dashboard_columns = _columns(bind, "dashboard_settings")
    if "fair_share_quota_mode_enabled" in dashboard_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column("fair_share_quota_mode_enabled")
