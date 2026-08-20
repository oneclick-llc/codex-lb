from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import update

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AccountUsageRollupState, RequestUsageHourlyRollup
from app.db.session import SessionLocal
from app.modules.accounts.usage_rollup import lock_fold_state
from app.modules.accounts.usage_time_rollup import (
    DIMENSION_SENTINEL,
    WARMUP_REQUEST_KINDS,
    HourlyUsageRollupRow,
    RequestUsageTimeRollupRepository,
    epoch_seconds,
    floor_to_hour,
    to_dimension,
)
from app.modules.accounts.usage_time_rollup_read import sum_hourly_cost_by_api_key_window
from app.modules.proxy.fair_share_quota import _long_window_cost_by_key
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


def _hourly_cost_row(bucket_epoch: int, *, api_key_id: str, model: str, request_kind: str, cost: float):
    return HourlyUsageRollupRow(
        bucket_epoch=bucket_epoch,
        account_id="acc_a",
        api_key_id=api_key_id,
        model=model,
        service_tier=DIMENSION_SENTINEL,
        request_kind=request_kind,
        is_deleted=False,
        request_count=1,
        cost_usd=cost,
        cost_count=1,
    )


@pytest.mark.asyncio
async def test_long_window_costs_aggregate_in_sql_across_watermark(db_setup):
    watermark = floor_to_hour(utcnow())

    async with SessionLocal() as session:
        await lock_fold_state(session)
        session.add(
            Account(
                id="acc_a",
                email="acc_a@example.com",
                plan_type="plus",
                access_token_encrypted=TokenEncryptor().encrypt("access"),
                refresh_token_encrypted=TokenEncryptor().encrypt("refresh"),
                id_token_encrypted=TokenEncryptor().encrypt("id"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )
        await session.commit()

    hour_1 = epoch_seconds(watermark - timedelta(hours=1))
    hour_2 = epoch_seconds(watermark - timedelta(hours=2))
    async with SessionLocal() as session:
        repo = RequestUsageTimeRollupRepository(session)
        await repo.add_hourly(
            [
                _hourly_cost_row(hour_1, api_key_id="k1", model="gpt-5.1-codex", request_kind="normal", cost=3.0),
                _hourly_cost_row(hour_2, api_key_id="k1", model="gpt-5.2", request_kind="normal", cost=2.0),
                _hourly_cost_row(hour_1, api_key_id="k2", model="gpt-5.1-codex", request_kind="normal", cost=1.0),
                # Excluded by the classifier's filters:
                _hourly_cost_row(
                    hour_1, api_key_id="k1", model="gpt-5.1-codex", request_kind="limit_warmup", cost=100.0
                ),
                _hourly_cost_row(
                    hour_1, api_key_id=DIMENSION_SENTINEL, model="gpt-5.1-codex", request_kind="normal", cost=50.0
                ),
            ]
        )
        await session.execute(update(AccountUsageRollupState).values(hourly_folded_through=watermark))
        await session.commit()

    # Raw tail beyond the watermark: one attributed row plus two excluded ones.
    async with SessionLocal() as session:
        logs_repo = RequestLogsRepository(session)
        now = utcnow()
        for request_id, api_key_id, request_kind, cost in (
            ("req-k1-raw", "k1", "normal", 1.0),
            ("req-warmup-raw", "k1", "limit_warmup", 9.0),
            ("req-anon-raw", None, "normal", 7.0),
        ):
            await logs_repo.add_log(
                account_id="acc_a",
                request_id=request_id,
                model="gpt-5.1-codex",
                input_tokens=10,
                output_tokens=5,
                reasoning_tokens=None,
                cached_input_tokens=0,
                latency_ms=100,
                status="success",
                error_code=None,
                requested_at=now,
                cost_usd=cost,
                request_kind=request_kind,
                service_tier=None,
                api_key_id=api_key_id,
                conversation_id=None,
            )
        await session.commit()

    async with SessionLocal() as session:
        folded, raw_windows = await sum_hourly_cost_by_api_key_window(
            session,
            utcnow() - timedelta(days=7),
            None,
            filters=(
                RequestUsageHourlyRollup.api_key_id != to_dimension(None),
                RequestUsageHourlyRollup.request_kind.not_in(WARMUP_REQUEST_KINDS),
            ),
        )
        # SQL-side aggregation: one summed entry per key, warmup and
        # unattributed rows excluded, raw complement returned for the tail.
        assert folded == {"k1": pytest.approx(5.0), "k2": pytest.approx(1.0)}
        assert raw_windows
        assert any(start == watermark for start, _end in raw_windows)

    async with SessionLocal() as session:
        costs = await _long_window_cost_by_key(session)
        assert costs == {"k1": pytest.approx(6.0), "k2": pytest.approx(1.0)}
