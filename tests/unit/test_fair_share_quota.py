from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import Request

import app.modules.proxy.fair_share_quota as fair_share_quota
from app.core.balancer import (
    TRAFFIC_CLASS_FAIR_SHARE_DEGRADED,
    TRAFFIC_CLASS_FOREGROUND,
    TRAFFIC_CLASS_OPPORTUNISTIC,
)
from app.core.utils.time import utcnow
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy import api as proxy_api
from app.modules.proxy.fair_share_quota import (
    FairShareQuotaClassifier,
    classify_over_share,
    resolve_effective_traffic_class,
)
from app.modules.proxy.load_balancer import AccountSelection

pytestmark = pytest.mark.unit


def _make_api_key(key_id: str, traffic_class: str = TRAFFIC_CLASS_FOREGROUND) -> ApiKeyData:
    return ApiKeyData(
        id=key_id,
        name=key_id,
        key_prefix="sk-test",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
        traffic_class=traffic_class,
    )


class TestClassifyOverShare:
    def test_equal_consumption_is_never_over_share(self) -> None:
        keys = frozenset({"a", "b", "c"})
        costs = {"a": 10.0, "b": 10.0, "c": 10.0}
        assert (
            classify_over_share(active_key_ids=keys, long_costs=costs, fast_costs=costs, previously_over=frozenset())
            == frozenset()
        )

    def test_heavy_key_enters_over_share(self) -> None:
        keys = frozenset({"a", "b", "c"})
        costs = {"a": 80.0, "b": 10.0, "c": 10.0}
        assert classify_over_share(
            active_key_ids=keys, long_costs=costs, fast_costs={}, previously_over=frozenset()
        ) == frozenset({"a"})

    def test_share_within_enter_tolerance_stays_foreground(self) -> None:
        keys = frozenset({"a", "b", "c"})
        # 38% < 40% enter threshold (1.2 x 1/3).
        costs = {"a": 38.0, "b": 31.0, "c": 31.0}
        assert (
            classify_over_share(active_key_ids=keys, long_costs=costs, fast_costs={}, previously_over=frozenset())
            == frozenset()
        )

    def test_hysteresis_keeps_key_degraded_until_back_at_fair_share(self) -> None:
        keys = frozenset({"a", "b", "c"})
        # 35% is between exit (33.3%) and enter (40%): stays degraded only if
        # it already was degraded.
        costs = {"a": 35.0, "b": 33.0, "c": 32.0}
        assert classify_over_share(
            active_key_ids=keys, long_costs=costs, fast_costs={}, previously_over=frozenset({"a"})
        ) == frozenset({"a"})
        assert (
            classify_over_share(active_key_ids=keys, long_costs=costs, fast_costs={}, previously_over=frozenset())
            == frozenset()
        )

    def test_recovered_key_is_restored(self) -> None:
        keys = frozenset({"a", "b", "c"})
        costs = {"a": 30.0, "b": 35.0, "c": 35.0}
        assert (
            classify_over_share(active_key_ids=keys, long_costs=costs, fast_costs={}, previously_over=frozenset({"a"}))
            == frozenset()
        )

    def test_fast_window_burst_triggers_alone(self) -> None:
        keys = frozenset({"a", "b", "c"})
        long_costs = {"a": 10.0, "b": 10.0, "c": 10.0}
        fast_costs = {"a": 99.0, "b": 1.0}
        assert classify_over_share(
            active_key_ids=keys, long_costs=long_costs, fast_costs=fast_costs, previously_over=frozenset()
        ) == frozenset({"a"})

    def test_single_key_is_never_degraded(self) -> None:
        assert (
            classify_over_share(
                active_key_ids=frozenset({"a"}),
                long_costs={"a": 100.0},
                fast_costs={"a": 100.0},
                previously_over=frozenset(),
            )
            == frozenset()
        )

    def test_zero_consumption_pool_degrades_nobody(self) -> None:
        keys = frozenset({"a", "b"})
        assert (
            classify_over_share(active_key_ids=keys, long_costs={}, fast_costs={}, previously_over=frozenset({"a"}))
            == frozenset()
        )

    def test_ghost_consumption_counts_in_denominator(self) -> None:
        keys = frozenset({"a", "b"})
        # "ghost" consumed most of the pool but is not active; active keys are
        # judged against k=3 consumers and the total including ghost's spend.
        costs = {"ghost": 80.0, "a": 15.0, "b": 5.0}
        assert (
            classify_over_share(active_key_ids=keys, long_costs=costs, fast_costs={}, previously_over=frozenset())
            == frozenset()
        )

    # --- sparse activity: idle keys must not make the working ones over-share

    def test_lone_consumer_in_burst_window_is_not_degraded(self) -> None:
        keys = frozenset({"a", "b", "c", "d", "e"})
        balanced = {key: 10.0 for key in keys}
        assert (
            classify_over_share(
                active_key_ids=keys, long_costs=balanced, fast_costs={"a": 0.5}, previously_over=frozenset()
            )
            == frozenset()
        )

    def test_two_equal_consumers_among_five_keys_are_not_degraded(self) -> None:
        keys = frozenset({"a", "b", "c", "d", "e"})
        balanced = {key: 10.0 for key in keys}
        assert (
            classify_over_share(
                active_key_ids=keys, long_costs=balanced, fast_costs={"a": 1.0, "b": 1.0}, previously_over=frozenset()
            )
            == frozenset()
        )

    def test_three_of_five_working_this_week_equally_are_not_degraded(self) -> None:
        keys = frozenset({"a", "b", "c", "d", "e"})
        assert (
            classify_over_share(
                active_key_ids=keys,
                long_costs={"a": 10.0, "b": 10.0, "c": 10.0},
                fast_costs={},
                previously_over=frozenset(),
            )
            == frozenset()
        )

    def test_heavy_consumer_among_two_active_is_degraded(self) -> None:
        keys = frozenset({"a", "b", "c", "d", "e"})
        # k=2 -> enter at >60%.
        assert classify_over_share(
            active_key_ids=keys, long_costs={"a": 70.0, "b": 30.0}, fast_costs={}, previously_over=frozenset()
        ) == frozenset({"a"})
        assert (
            classify_over_share(
                active_key_ids=keys, long_costs={"a": 55.0, "b": 45.0}, fast_costs={}, previously_over=frozenset()
            )
            == frozenset()
        )

    def test_window_below_min_total_is_noise(self) -> None:
        keys = frozenset({"a", "b"})
        assert (
            classify_over_share(
                active_key_ids=keys, long_costs={}, fast_costs={"a": 0.01, "b": 0.001}, previously_over=frozenset()
            )
            == frozenset()
        )
        # Same ratio above the floor is contention.
        assert classify_over_share(
            active_key_ids=keys, long_costs={}, fast_costs={"a": 1.0, "b": 0.1}, previously_over=frozenset()
        ) == frozenset({"a"})

    def test_noise_window_releases_previously_degraded_key(self) -> None:
        keys = frozenset({"a", "b"})
        assert (
            classify_over_share(
                active_key_ids=keys, long_costs={}, fast_costs={"a": 0.01}, previously_over=frozenset({"a"})
            )
            == frozenset()
        )


class TestClassifierCache:
    @pytest.fixture(autouse=True)
    def _stub_session(self, monkeypatch: pytest.MonkeyPatch):
        @asynccontextmanager
        async def _fake_session():
            yield None

        monkeypatch.setattr(fair_share_quota, "get_background_session", _fake_session)

    def _stub_queries(self, monkeypatch: pytest.MonkeyPatch, *, active, long_costs, fast_costs, calls=None):
        async def _active(_session):
            if calls is not None:
                calls.append("refresh")
            return frozenset(active)

        async def _long(_session):
            return dict(long_costs)

        async def _fast(_session):
            return dict(fast_costs)

        monkeypatch.setattr(fair_share_quota, "_active_foreground_key_ids", _active)
        monkeypatch.setattr(fair_share_quota, "_long_window_cost_by_key", _long)
        monkeypatch.setattr(fair_share_quota, "_fast_window_cost_by_key", _fast)

    @pytest.mark.asyncio
    async def test_refresh_classifies_and_lookup_serves_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        self._stub_queries(
            monkeypatch,
            active={"a", "b", "c"},
            long_costs={"a": 80.0, "b": 10.0, "c": 10.0},
            fast_costs={},
            calls=calls,
        )
        classifier = FairShareQuotaClassifier(cache_ttl_seconds=60.0)
        await classifier.refresh()
        assert await classifier.is_over_share("a") is True
        assert await classifier.is_over_share("b") is False
        assert calls == ["refresh"]

    @pytest.mark.asyncio
    async def test_lookup_never_blocks_and_schedules_single_flight_refresh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stub_queries(
            monkeypatch,
            active={"a", "b", "c"},
            long_costs={"a": 80.0, "b": 10.0, "c": 10.0},
            fast_costs={},
        )
        classifier = FairShareQuotaClassifier(cache_ttl_seconds=60.0)
        # Cold lookup answers immediately (no degradation) and schedules one
        # background refresh; a second lookup reuses the in-flight task.
        assert await classifier.is_over_share("a") is False
        task = classifier._refresh_task
        assert task is not None and not task.done()
        assert await classifier.is_over_share("a") is False
        assert classifier._refresh_task is task
        await task
        assert await classifier.is_over_share("a") is True

    @pytest.mark.asyncio
    async def test_stale_snapshot_serves_while_revalidating(self, monkeypatch: pytest.MonkeyPatch) -> None:
        classifier = FairShareQuotaClassifier(cache_ttl_seconds=0.0)
        self._stub_queries(
            monkeypatch,
            active={"a", "b", "c"},
            long_costs={"a": 80.0, "b": 10.0, "c": 10.0},
            fast_costs={},
        )
        await classifier.refresh()
        self._stub_queries(
            monkeypatch,
            active={"a", "b", "c"},
            long_costs={"a": 20.0, "b": 40.0, "c": 40.0},
            fast_costs={},
        )
        # Expired snapshot still answers instantly with the stale verdict.
        assert await classifier.is_over_share("a") is True
        task = classifier._refresh_task
        assert task is not None
        await task
        assert await classifier.is_over_share("a") is False

    @pytest.mark.asyncio
    async def test_hysteresis_across_refreshes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        classifier = FairShareQuotaClassifier(cache_ttl_seconds=60.0)
        self._stub_queries(
            monkeypatch,
            active={"a", "b", "c"},
            long_costs={"a": 80.0, "b": 10.0, "c": 10.0},
            fast_costs={},
        )
        await classifier.refresh()
        assert await classifier.is_over_share("a") is True
        # Drop to 35%: between exit and enter, so the prior degradation holds.
        self._stub_queries(
            monkeypatch,
            active={"a", "b", "c"},
            long_costs={"a": 35.0, "b": 33.0, "c": 32.0},
            fast_costs={},
        )
        await classifier.refresh()
        assert await classifier.is_over_share("a") is True
        # Back at fair share: restored.
        self._stub_queries(
            monkeypatch,
            active={"a", "b", "c"},
            long_costs={"a": 33.0, "b": 34.0, "c": 33.0},
            fast_costs={},
        )
        await classifier.refresh()
        assert await classifier.is_over_share("a") is False

    @pytest.mark.asyncio
    async def test_refresh_failure_fails_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(_session):
            raise RuntimeError("rollup read failed")

        monkeypatch.setattr(fair_share_quota, "_active_foreground_key_ids", _boom)
        classifier = FairShareQuotaClassifier(cache_ttl_seconds=60.0)
        await classifier.refresh()
        assert await classifier.is_over_share("a") is False

    @pytest.mark.asyncio
    async def test_hung_refresh_times_out_fails_open_and_releases_single_flight(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        classifier = FairShareQuotaClassifier(cache_ttl_seconds=60.0)
        self._stub_queries(
            monkeypatch,
            active={"a", "b", "c"},
            long_costs={"a": 80.0, "b": 10.0, "c": 10.0},
            fast_costs={},
        )
        await classifier.refresh()
        assert await classifier.is_over_share("a") is True

        async def _hang(_session):
            await asyncio.Event().wait()

        monkeypatch.setattr(fair_share_quota, "_active_foreground_key_ids", _hang)
        monkeypatch.setattr(fair_share_quota, "REFRESH_TIMEOUT_SECONDS", 0.01)
        # A hung read times out and fails open (degradations dropped)...
        await classifier.refresh()
        assert await classifier.is_over_share("a") is False
        # ...and a scheduled background refresh finishes via the timeout too,
        # releasing the single-flight slot instead of pinning it forever.
        classifier._snapshot = None
        assert await classifier.is_over_share("a") is False
        task = classifier._refresh_task
        assert task is not None
        await task
        assert task.done()

    @pytest.mark.asyncio
    async def test_refresh_failure_drops_stale_degradations(self, monkeypatch: pytest.MonkeyPatch) -> None:
        classifier = FairShareQuotaClassifier(cache_ttl_seconds=60.0)
        self._stub_queries(
            monkeypatch,
            active={"a", "b", "c"},
            long_costs={"a": 80.0, "b": 10.0, "c": 10.0},
            fast_costs={},
        )
        await classifier.refresh()
        assert await classifier.is_over_share("a") is True

        async def _boom(_session):
            raise RuntimeError("rollup read failed")

        monkeypatch.setattr(fair_share_quota, "_active_foreground_key_ids", _boom)
        await classifier.refresh()
        # A broken read must not keep the key degraded.
        assert await classifier.is_over_share("a") is False


class TestSettingsInvalidationCallback:
    def _stub_settings(self, monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> None:
        async def _get():
            return SimpleNamespace(fair_share_quota_mode_enabled=enabled)

        monkeypatch.setattr(fair_share_quota, "get_settings_cache", lambda: SimpleNamespace(get=_get))

    @pytest.mark.asyncio
    async def test_idle_worker_drops_snapshot_when_mode_turned_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # No admission request ever reaches this worker; only the settings bump does.
        classifier = FairShareQuotaClassifier(cache_ttl_seconds=60.0)
        classifier._set_snapshot(frozenset({"k1", "k2"}), 5)
        monkeypatch.setattr(fair_share_quota, "get_fair_share_quota_classifier", lambda: classifier)
        self._stub_settings(monkeypatch, enabled=False)

        await fair_share_quota.reset_fair_share_quota_classifier_if_mode_disabled()

        assert classifier._snapshot is None
        if fair_share_quota.PROMETHEUS_AVAILABLE and fair_share_quota.fair_share_quota_over_share_keys is not None:
            assert fair_share_quota.fair_share_quota_over_share_keys._value.get() == 0

    @pytest.mark.asyncio
    async def test_unrelated_settings_save_keeps_snapshot_while_mode_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        classifier = FairShareQuotaClassifier(cache_ttl_seconds=60.0)
        classifier._set_snapshot(frozenset({"k1"}), 3)
        monkeypatch.setattr(fair_share_quota, "get_fair_share_quota_classifier", lambda: classifier)
        self._stub_settings(monkeypatch, enabled=True)

        await fair_share_quota.reset_fair_share_quota_classifier_if_mode_disabled()

        assert classifier._snapshot is not None
        assert classifier._snapshot.over_share_key_ids == frozenset({"k1"})


class TestResolveEffectiveTrafficClass:
    def _stub_settings(self, monkeypatch: pytest.MonkeyPatch, *, enabled: bool):
        async def _get():
            return SimpleNamespace(fair_share_quota_mode_enabled=enabled)

        monkeypatch.setattr(fair_share_quota, "get_settings_cache", lambda: SimpleNamespace(get=_get))

    def _stub_classifier(self, monkeypatch: pytest.MonkeyPatch, over_share_ids: set[str]):
        async def _is_over_share(key_id: str) -> bool:
            return key_id in over_share_ids

        monkeypatch.setattr(
            fair_share_quota,
            "get_fair_share_quota_classifier",
            lambda: SimpleNamespace(is_over_share=_is_over_share, reset_if_populated=lambda: None),
        )

    @pytest.mark.asyncio
    async def test_no_api_key_passes_requested_through(self) -> None:
        assert await resolve_effective_traffic_class(None) == TRAFFIC_CLASS_FOREGROUND

    @pytest.mark.asyncio
    async def test_static_opportunistic_key_stays_opportunistic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fail():  # settings must not be consulted for static keys
            raise AssertionError("settings cache should not be read")

        monkeypatch.setattr(fair_share_quota, "get_settings_cache", _fail)
        api_key = _make_api_key("k1", traffic_class=TRAFFIC_CLASS_OPPORTUNISTIC)
        assert await resolve_effective_traffic_class(api_key) == TRAFFIC_CLASS_OPPORTUNISTIC

    @pytest.mark.asyncio
    async def test_mode_disabled_keeps_foreground(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stub_settings(monkeypatch, enabled=False)
        self._stub_classifier(monkeypatch, {"k1"})
        assert await resolve_effective_traffic_class(_make_api_key("k1")) == TRAFFIC_CLASS_FOREGROUND

    @pytest.mark.asyncio
    async def test_mode_enabled_degrades_over_share_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stub_settings(monkeypatch, enabled=True)
        self._stub_classifier(monkeypatch, {"k1"})
        assert await resolve_effective_traffic_class(_make_api_key("k1")) == TRAFFIC_CLASS_FAIR_SHARE_DEGRADED

    @pytest.mark.asyncio
    async def test_mode_enabled_keeps_under_share_key_foreground(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stub_settings(monkeypatch, enabled=True)
        self._stub_classifier(monkeypatch, {"other"})
        assert await resolve_effective_traffic_class(_make_api_key("k1")) == TRAFFIC_CLASS_FOREGROUND

    @pytest.mark.asyncio
    async def test_opportunistic_requested_class_is_preserved(self) -> None:
        api_key = _make_api_key("k1")
        result = await resolve_effective_traffic_class(api_key, requested=TRAFFIC_CLASS_OPPORTUNISTIC)
        assert result == TRAFFIC_CLASS_OPPORTUNISTIC

    @pytest.mark.asyncio
    async def test_disabling_mode_resets_cached_classification(self, monkeypatch: pytest.MonkeyPatch) -> None:
        classifier = FairShareQuotaClassifier(cache_ttl_seconds=60.0)
        classifier._set_snapshot(frozenset({"k1"}), 3)
        monkeypatch.setattr(fair_share_quota, "get_fair_share_quota_classifier", lambda: classifier)

        self._stub_settings(monkeypatch, enabled=True)
        assert await resolve_effective_traffic_class(_make_api_key("k1")) == TRAFFIC_CLASS_FAIR_SHARE_DEGRADED

        self._stub_settings(monkeypatch, enabled=False)
        assert await resolve_effective_traffic_class(_make_api_key("k1")) == TRAFFIC_CLASS_FOREGROUND
        # The cached over-share verdict (and its gauge) is dropped, not kept.
        assert classifier._snapshot is None


class TestAdmissionRoutePath:
    """Degraded foreground keys flow through the opportunistic admission gate."""

    def _stub_mode(self, monkeypatch: pytest.MonkeyPatch, *, enabled: bool, over_share_ids: set[str]):
        async def _get():
            return SimpleNamespace(fair_share_quota_mode_enabled=enabled)

        async def _is_over_share(key_id: str) -> bool:
            return key_id in over_share_ids

        monkeypatch.setattr(fair_share_quota, "get_settings_cache", lambda: SimpleNamespace(get=_get))
        monkeypatch.setattr(
            fair_share_quota,
            "get_fair_share_quota_classifier",
            lambda: SimpleNamespace(is_over_share=_is_over_share, reset_if_populated=lambda: None),
        )

    def _request(self) -> Request:
        return Request({"type": "http", "method": "POST", "path": "/v1/responses", "headers": []})

    @pytest.mark.asyncio
    async def test_over_share_foreground_key_denied_when_burn_window_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stub_mode(monkeypatch, enabled=True, over_share_ids={"k1"})
        selection = AccountSelection(
            account=None,
            error_message="opportunistic burn window closed: no account within budget",
            error_code="opportunistic_burn_window_closed",
        )
        service = SimpleNamespace(check_opportunistic_admission=AsyncMock(return_value=selection))
        context = cast(proxy_api.ProxyContext, SimpleNamespace(service=service))

        response = await proxy_api._opportunistic_admission_denial(
            self._request(), context, _make_api_key("k1"), model="gpt-5.1"
        )

        assert response is not None
        assert response.status_code == 429
        body = json.loads(bytes(response.body))
        assert body["error"]["code"] == "rate_limit_exceeded"
        assert body["error"]["message"].startswith("opportunistic burn window closed")
        assert response.headers["Retry-After"] == "60"

    @pytest.mark.asyncio
    async def test_over_share_foreground_key_admitted_into_open_headroom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stub_mode(monkeypatch, enabled=True, over_share_ids={"k1"})
        selection = AccountSelection(account=SimpleNamespace(id="acc"), error_message=None)
        service = SimpleNamespace(check_opportunistic_admission=AsyncMock(return_value=selection))
        context = cast(proxy_api.ProxyContext, SimpleNamespace(service=service))

        response = await proxy_api._opportunistic_admission_denial(
            self._request(), context, _make_api_key("k1"), model="gpt-5.1"
        )

        assert response is None
        service.check_opportunistic_admission.assert_awaited_once()
        gate_kwargs = service.check_opportunistic_admission.await_args.kwargs
        assert gate_kwargs["traffic_class"] == TRAFFIC_CLASS_FAIR_SHARE_DEGRADED

    @pytest.mark.asyncio
    async def test_mode_off_foreground_key_bypasses_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stub_mode(monkeypatch, enabled=False, over_share_ids={"k1"})
        service = SimpleNamespace(check_opportunistic_admission=AsyncMock())
        context = cast(proxy_api.ProxyContext, SimpleNamespace(service=service))

        response = await proxy_api._opportunistic_admission_denial(
            self._request(), context, _make_api_key("k1"), model="gpt-5.1"
        )

        assert response is None
        service.check_opportunistic_admission.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mode_on_under_share_key_bypasses_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stub_mode(monkeypatch, enabled=True, over_share_ids={"other"})
        service = SimpleNamespace(check_opportunistic_admission=AsyncMock())
        context = cast(proxy_api.ProxyContext, SimpleNamespace(service=service))

        response = await proxy_api._opportunistic_admission_denial(
            self._request(), context, _make_api_key("k1"), model="gpt-5.1"
        )

        assert response is None
        service.check_opportunistic_admission.assert_not_awaited()
