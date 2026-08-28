"""merge fair-share quota mode and retry-circuit admission generation heads

Revision ID: 20260827_000000_merge_fair_share_and_retry_circuit_heads
Revises:
- 20260820_000000_add_fair_share_quota_mode
- 20260821_000000_add_retry_circuit_admission_generation
Create Date: 2026-08-27 00:00:00.000000
"""

from __future__ import annotations

revision = "20260827_000000_merge_fair_share_and_retry_circuit_heads"
down_revision = (
    "20260820_000000_add_fair_share_quota_mode",
    "20260821_000000_add_retry_circuit_admission_generation",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
