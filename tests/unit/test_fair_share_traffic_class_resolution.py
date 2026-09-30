"""One request resolves its effective traffic class exactly once.

The fair-share classifier snapshot is refreshed by a background single-flight
task, so resolving again later in the same request can return a different
verdict. Admission-time classification is per request and in-flight turns are
never reclassified, so the admission pre-gate's verdict must win for every
selection that follows it in that request.
"""

from __future__ import annotations

import json
import time
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import Request

import app.modules.proxy.fair_share_quota as fair_share_quota
from app.core.balancer import (
    TRAFFIC_CLASS_FAIR_SHARE_DEGRADED,
    TRAFFIC_CLASS_FOREGROUND,
    TRAFFIC_CLASS_OPPORTUNISTIC,
)
from app.core.utils.request_id import set_request_id
from app.core.utils.time import utcnow
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy import api as proxy_api
from app.modules.proxy import request_traffic_class
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service import codex_control as codex_control_module
from app.modules.proxy.load_balancer import AccountSelection
from app.modules.proxy.request_traffic_class import resolve_request_traffic_class

pytestmark = pytest.mark.unit

_DENIAL_DETAIL = "7d 38% (fair 17%), 1h 5% (uncontended)"


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


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/v1/responses", "headers": []})


class _FlippingClassifier:
    """Classifier whose verdict changes between lookups, the way a background
    refresh landing mid-request would."""

    def __init__(self, *verdicts: bool) -> None:
        self._verdicts = verdicts
        self.calls = 0

    async def is_over_share(self, api_key_id: str) -> bool:
        verdict = self._verdicts[min(self.calls, len(self._verdicts) - 1)]
        self.calls += 1
        return verdict

    def reset_if_populated(self) -> None:
        return None

    def denial_detail(self, api_key_id: str) -> str | None:
        return _DENIAL_DETAIL


def _stub_fair_share(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool,
    classifier: _FlippingClassifier,
) -> None:
    async def _get() -> Any:
        return SimpleNamespace(fair_share_quota_mode_enabled=enabled)

    monkeypatch.setattr(fair_share_quota, "get_settings_cache", lambda: SimpleNamespace(get=_get))
    monkeypatch.setattr(fair_share_quota, "get_fair_share_quota_classifier", lambda: classifier)


async def _pre_gate(
    api_key: ApiKeyData,
    *,
    selection: AccountSelection,
) -> tuple[Any, AsyncMock]:
    gate = AsyncMock(return_value=selection)
    context = cast(proxy_api.ProxyContext, SimpleNamespace(service=SimpleNamespace(check_opportunistic_admission=gate)))
    response = await proxy_api._opportunistic_admission_denial(_request(), context, api_key, model="gpt-5.1")
    return response, gate


def _admitting_selection() -> AccountSelection:
    return AccountSelection(account=cast(Any, SimpleNamespace(id="acc")), error_message=None)


@pytest.mark.asyncio
async def test_selection_reuses_the_pre_gate_verdict_when_the_classifier_flips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A turn admitted as degraded stays degraded at selection time."""
    classifier = _FlippingClassifier(True, False)
    _stub_fair_share(monkeypatch, enabled=True, classifier=classifier)
    set_request_id("req-degraded-then-under-share")
    api_key = _make_api_key("k1")

    response, gate = await _pre_gate(api_key, selection=_admitting_selection())

    assert response is None
    assert gate.await_args is not None
    assert gate.await_args.kwargs["traffic_class"] == TRAFFIC_CLASS_FAIR_SHARE_DEGRADED

    # The classifier now says the key is under share; the in-flight turn keeps
    # the class it was admitted with instead of being reclassified.
    assert await resolve_request_traffic_class(api_key) == TRAFFIC_CLASS_FAIR_SHARE_DEGRADED
    assert classifier.calls == 1


@pytest.mark.asyncio
async def test_selection_is_not_degraded_after_a_foreground_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reverse flip: a foreground admission is never degraded mid-request."""
    classifier = _FlippingClassifier(False, True)
    _stub_fair_share(monkeypatch, enabled=True, classifier=classifier)
    set_request_id("req-under-share-then-over-share")
    api_key = _make_api_key("k1")

    response, gate = await _pre_gate(api_key, selection=_admitting_selection())

    assert response is None
    gate.assert_not_awaited()
    assert await resolve_request_traffic_class(api_key) == TRAFFIC_CLASS_FOREGROUND
    assert classifier.calls == 1


@pytest.mark.asyncio
async def test_account_selection_uses_the_pinned_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: the real selection pass inherits the pre-gate's verdict."""
    classifier = _FlippingClassifier(True, False)
    _stub_fair_share(monkeypatch, enabled=True, classifier=classifier)
    set_request_id("req-selection")
    api_key = _make_api_key("k1")

    response, _ = await _pre_gate(api_key, selection=_admitting_selection())
    assert response is None

    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    select_account = AsyncMock(return_value=_admitting_selection())
    service._load_balancer = cast(Any, SimpleNamespace(select_account=select_account))
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(
                    sticky_reallocation_budget_threshold_pct=95.0,
                    fair_share_quota_mode_enabled=True,
                )
            )
        ),
    )

    await service._select_account_with_budget(
        time.monotonic() + 60.0,
        request_id="req-selection",
        kind="stream",
        api_key=api_key,
    )

    assert select_account.await_args is not None
    assert select_account.await_args.kwargs["traffic_class"] == TRAFFIC_CLASS_FAIR_SHARE_DEGRADED
    assert classifier.calls == 1


@pytest.mark.asyncio
async def test_next_request_is_reclassified(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pinning is per request: the next turn picks up the newer snapshot."""
    classifier = _FlippingClassifier(True, False)
    _stub_fair_share(monkeypatch, enabled=True, classifier=classifier)
    api_key = _make_api_key("k1")

    set_request_id("req-first")
    assert await resolve_request_traffic_class(api_key) == TRAFFIC_CLASS_FAIR_SHARE_DEGRADED

    set_request_id("req-second")
    assert await resolve_request_traffic_class(api_key) == TRAFFIC_CLASS_FOREGROUND
    assert classifier.calls == 2


@pytest.mark.asyncio
async def test_fair_share_denial_still_carries_the_share_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    classifier = _FlippingClassifier(True)
    _stub_fair_share(monkeypatch, enabled=True, classifier=classifier)
    set_request_id("req-denied")
    selection = AccountSelection(
        account=None,
        error_message="opportunistic burn window closed: no account within budget",
        error_code="opportunistic_burn_window_closed",
    )

    response, _ = await _pre_gate(_make_api_key("k1"), selection=selection)

    assert response is not None
    assert response.status_code == 429
    message = json.loads(bytes(response.body))["error"]["message"]
    assert message.endswith(f"your key's share of pooled usage: {_DENIAL_DETAIL}")


@pytest.mark.asyncio
async def test_mode_disabled_never_consults_the_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    classifier = _FlippingClassifier(True)
    _stub_fair_share(monkeypatch, enabled=False, classifier=classifier)
    set_request_id("req-mode-off")
    api_key = _make_api_key("k1")

    response, gate = await _pre_gate(api_key, selection=_admitting_selection())

    assert response is None
    gate.assert_not_awaited()
    assert await resolve_request_traffic_class(api_key) == TRAFFIC_CLASS_FOREGROUND
    assert classifier.calls == 0


@pytest.mark.asyncio
async def test_static_opportunistic_key_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail() -> Any:  # settings must not be read for a statically classed key
        raise AssertionError("settings cache should not be read")

    monkeypatch.setattr(fair_share_quota, "get_settings_cache", _fail)
    set_request_id("req-static-opportunistic")
    api_key = _make_api_key("k1", traffic_class=TRAFFIC_CLASS_OPPORTUNISTIC)

    assert await resolve_request_traffic_class(api_key) == TRAFFIC_CLASS_OPPORTUNISTIC


@pytest.mark.asyncio
async def test_requested_non_foreground_class_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    classifier = _FlippingClassifier(True)
    _stub_fair_share(monkeypatch, enabled=True, classifier=classifier)
    set_request_id("req-requested-opportunistic")

    resolved = await resolve_request_traffic_class(_make_api_key("k1"), requested=TRAFFIC_CLASS_OPPORTUNISTIC)

    assert resolved == TRAFFIC_CLASS_OPPORTUNISTIC
    assert classifier.calls == 0


def test_no_request_path_module_can_silently_re_resolve() -> None:
    """Only the pinning module owns the unpinned primitive."""
    for module in (proxy_api, proxy_service, codex_control_module):
        assert not hasattr(module, "resolve_effective_traffic_class"), module.__name__
        assert module.resolve_request_traffic_class is request_traffic_class.resolve_request_traffic_class
