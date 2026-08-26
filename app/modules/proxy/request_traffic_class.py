"""The one place a request resolves its effective traffic class.

The fair-share classifier snapshot is refreshed by a background single-flight
task, so resolving twice within one request can return two different verdicts:
a turn admitted as foreground would be re-resolved as degraded at selection
time (dying at a closed burn window after its API-key reservation was already
taken, and without the share detail the admission pre-gate attaches), while the
reverse flip would deny against a snapshot that no longer classifies the key.
Classification is admission-time only and in-flight turns are never
reclassified, so the first resolution of a request wins for every admission and
selection call that follows it.

This is a leaf module so the admission pre-gate (``proxy.api``), the selection
path (``proxy.service``) and the codex-control path can share one entry point
without importing each other. Request-path code calls
``resolve_request_traffic_class``; ``resolve_effective_traffic_class`` is the
unpinned primitive and belongs to this module only.
"""

from __future__ import annotations

from contextvars import ContextVar

from app.core.balancer import TRAFFIC_CLASS_FOREGROUND, TrafficClass
from app.core.utils.request_id import get_request_id
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.fair_share_quota import resolve_effective_traffic_class
from app.modules.settings.service import DashboardSettingsData

_PINNED: ContextVar[tuple[tuple[str, str], TrafficClass] | None] = ContextVar(
    "proxy_request_traffic_class", default=None
)


async def resolve_request_traffic_class(
    api_key: ApiKeyData | None,
    *,
    requested: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
    settings: DashboardSettingsData | None = None,
) -> TrafficClass:
    """Effective traffic class for the current request, pinned at first resolution.

    Requests without an API key, without a request id, or that already ask for
    a non-foreground class never consult the classifier, so they stay a cheap
    pass-through and are not pinned.
    """
    request_id = get_request_id()
    if api_key is None or request_id is None or requested != TRAFFIC_CLASS_FOREGROUND:
        return await resolve_effective_traffic_class(api_key, requested=requested, settings=settings)
    cache_key = (request_id, api_key.id)
    pinned = _PINNED.get()
    if pinned is not None and pinned[0] == cache_key:
        return pinned[1]
    resolved = await resolve_effective_traffic_class(api_key, requested=requested, settings=settings)
    _PINNED.set((cache_key, resolved))
    return resolved
