"""codex_lb_upstream_requests_total records one sample per finished proxy request.

The counter is labelled by account and by outcome: ``success`` or the error
code of a failed request, so the share of ``server_error`` per account can be
computed from Prometheus.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.metrics import prometheus as metrics
from app.modules.proxy._service import request_log as request_log_module
from app.modules.proxy.helpers import _QUOTA_CODES, _RATE_LIMIT_CODES, _TRANSIENT_CODES

pytestmark = pytest.mark.unit

if not metrics.PROMETHEUS_AVAILABLE:  # pragma: no cover - metrics extra not installed
    pytest.skip("prometheus_client is not installed", allow_module_level=True)


def _sample(account_id: str, status: str) -> float:
    registry = cast(Any, metrics.REGISTRY)
    value = registry.get_sample_value(
        "codex_lb_upstream_requests_total",
        {"account_id": account_id, "status": status},
    )
    return value or 0.0


def test_success_is_counted_per_account() -> None:
    before = _sample("acc-outcome-1", "success")
    request_log_module._record_upstream_outcome("acc-outcome-1", "success", None)
    assert _sample("acc-outcome-1", "success") == before + 1


def test_error_is_counted_under_its_code() -> None:
    before = _sample("acc-outcome-2", "server_error")
    request_log_module._record_upstream_outcome("acc-outcome-2", "error", "server_error")
    assert _sample("acc-outcome-2", "server_error") == before + 1


def test_unknown_codes_share_one_other_series() -> None:
    before = _sample("acc-outcome-3", "other")
    for index in range(50):
        request_log_module._record_upstream_outcome("acc-outcome-3", "error", f"err_unique_{'x' * index}")
    request_log_module._record_upstream_outcome("acc-outcome-3", "error", "Weird Code/With Spaces")
    assert _sample("acc-outcome-3", "other") == before + 51
    assert _sample("acc-outcome-3", "err_unique_") == 0.0


@pytest.mark.parametrize(
    "code",
    sorted(
        _RATE_LIMIT_CODES
        | _QUOTA_CODES
        | _TRANSIENT_CODES
        | {"stream_event_too_large", "invalid_api_key", "token_invalidated", "forbidden"}
    ),
)
def test_known_upstream_codes_keep_their_own_series(code: str) -> None:
    before = _sample("acc-outcome-7", code)
    request_log_module._record_upstream_outcome("acc-outcome-7", "error", code)
    assert _sample("acc-outcome-7", code) == before + 1


def test_error_code_success_is_not_counted_as_success() -> None:
    before_success = _sample("acc-outcome-6", "success")
    before_other = _sample("acc-outcome-6", "other")
    request_log_module._record_upstream_outcome("acc-outcome-6", "error", "success")
    assert _sample("acc-outcome-6", "success") == before_success
    assert _sample("acc-outcome-6", "other") == before_other + 1


def test_status_without_code_is_counted_under_status() -> None:
    before = _sample("acc-outcome-4", "cancelled")
    request_log_module._record_upstream_outcome("acc-outcome-4", "cancelled", None)
    assert _sample("acc-outcome-4", "cancelled") == before + 1


def test_request_without_account_is_not_counted() -> None:
    before = _sample("None", "no_accounts")
    request_log_module._record_upstream_outcome(None, "error", "no_accounts")
    assert _sample("None", "no_accounts") == before


@pytest.mark.asyncio
async def test_write_request_log_counts_the_outcome_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str | None, str, str | None]] = []
    monkeypatch.setattr(
        request_log_module,
        "_record_upstream_outcome",
        lambda account_id, status, error_code: calls.append((account_id, status, error_code)),
    )

    async def _noop_persist(**_kwargs: Any) -> None:
        return None

    service = SimpleNamespace(
        _persist_request_log=_noop_persist,
        _track_request_log_task=lambda task, **_kwargs: None,
        _request_log_tasks=set(),
        _background_cleanup_tasks=set(),
    )

    await request_log_module._RequestLogMixin._write_request_log(
        cast(Any, service),
        account_id="acc-outcome-5",
        api_key=None,
        request_id="req-outcome-5",
        model="gpt-6.1-sol",
        latency_ms=10,
        status="error",
        error_code="upstream_error",
        upstream_error_code="server_error",
    )
    await asyncio.sleep(0)

    assert calls == [("acc-outcome-5", "error", "server_error")]
