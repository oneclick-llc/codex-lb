from __future__ import annotations

import pytest
from alembic import command
from anyio import to_thread
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import _build_alembic_config, inspect_migration_state, run_upgrade

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_fair_share_quota_migration_upgrade_defaults_and_downgrade(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'fair_share_quota.sqlite'}"
    parent = "20260816_000000_add_model_source_embeddings"
    revision = "20260820_000000_add_fair_share_quota_mode"
    column = "fair_share_quota_mode_enabled"

    async def column_and_rows(engine):
        async with engine.connect() as connection:
            columns = {row[1] for row in await connection.execute(text("PRAGMA table_info('dashboard_settings')"))}
            rows = []
            if column in columns:
                rows = (
                    await connection.execute(text(f"SELECT {column} FROM dashboard_settings"))  # noqa: S608
                ).all()
            return columns, rows

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent, bootstrap_legacy=False))
    engine = create_async_engine(db_url)
    try:
        columns, _ = await column_and_rows(engine)
        assert column not in columns

        await to_thread.run_sync(lambda: run_upgrade(db_url, revision, bootstrap_legacy=False))
        columns, rows = await column_and_rows(engine)
        assert column in columns
        assert all(row == (0,) for row in rows)

        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), parent))
        columns, _ = await column_and_rows(engine)
        assert column not in columns

        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        assert result.current_revision == inspect_migration_state(db_url).head_revision
        columns, _ = await column_and_rows(engine)
        assert column in columns
    finally:
        await engine.dispose()
