"""Relative fair-share quota classification (dashboard ``fair_share_quota_mode``).

When the mode is enabled, every active foreground API key is classified by its
share of pooled ``cost_usd`` consumption among the keys that actually consumed
in a window. With ``k`` consuming keys, a key whose share exceeds
``ENTER_TOLERANCE x (1/k)`` in either the rolling long window or the rolling
burst window is degraded to ``fair_share_degraded`` admission (pace-floor
headroom only, see ``app.core.balancer``) until its share returns to at most
``EXIT_TOLERANCE x (1/k)`` in both windows. A lone consumer (``k == 1``) has
nobody to share with and is never degraded; a window whose total spend is
below ``MIN_WINDOW_TOTAL_USD`` is noise, not contention, and classifies nobody.
Idle keys (vacation, CI/bot keys that never call) neither dilute nor inflate
anyone's share, so adding a team member needs no per-key configuration.

Consumption attribution reuses the request-usage hourly rollups for the folded
history plus the raw ``request_logs`` tail beyond the fold watermark (long
window), and raw ``request_logs`` alone for the burst window. Warmup traffic
and rows without API-key attribution are excluded; the denominator is the total
attributed pool consumption of the window.

Classification is admission-time only and cached per replica; the classifier
never writes. Lookups are non-blocking (stale results serve while a
single-flight background refresh runs) and a failed refresh fails open by
dropping all degradations, so a broken or slow rollup read can never block or
throttle foreground traffic.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.balancer import (
    TRAFFIC_CLASS_FAIR_SHARE_DEGRADED,
    TRAFFIC_CLASS_FOREGROUND,
    TRAFFIC_CLASS_OPPORTUNISTIC,
    TrafficClass,
)
from app.core.config.settings_cache import get_settings_cache
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    fair_share_quota_degradations_total,
    fair_share_quota_over_share_keys,
)
from app.core.utils.time import utcnow
from app.db.models import ApiKey, RequestLog, RequestUsageHourlyRollup
from app.db.session import get_background_session
from app.modules.accounts.usage_time_rollup import WARMUP_REQUEST_KINDS, from_dimension, to_dimension
from app.modules.accounts.usage_time_rollup_read import raw_windows_clause, sum_hourly_cost_by_api_key_window
from app.modules.api_keys.service import ApiKeyData
from app.modules.settings.service import DashboardSettingsData

logger = logging.getLogger(__name__)

LONG_WINDOW = timedelta(days=7)
FAST_WINDOW = timedelta(hours=1)
# ponytail: fixed tolerances, promote to settings only if operators ask.
ENTER_TOLERANCE = 1.2
EXIT_TOLERANCE = 1.0
# Below this much total spend a window is noise: one $0.01 ping next to a $0.001
# ping is a 91% "share" that means nothing and would only flap the hysteresis.
MIN_WINDOW_TOTAL_USD = 0.05
CACHE_TTL_SECONDS = 60.0
# A hung usage read must release the single-flight refresh instead of pinning
# a stale over-share snapshot forever; timing out fails open like any error.
REFRESH_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class WindowShare:
    """A key's fraction of one window's pooled spend, and the fair share it is
    judged against (``None`` when the window classifies nobody: a lone
    consumer or total spend below the noise floor)."""

    share: float
    fair: float | None


@dataclass(frozen=True, slots=True)
class KeyShares:
    long: WindowShare
    fast: WindowShare


@dataclass(frozen=True, slots=True)
class FairShareQuotaSnapshot:
    over_share_key_ids: frozenset[str]
    active_foreground_key_count: int
    computed_monotonic: float
    shares: Mapping[str, KeyShares] = field(default_factory=dict)


def window_share(
    costs: dict[str, float], key_id: str, *, min_window_total: float = MIN_WINDOW_TOTAL_USD
) -> WindowShare:
    total = sum(max(0.0, cost) for cost in costs.values())
    share = max(0.0, costs.get(key_id, 0.0)) / total if total > 0.0 else 0.0
    if total < min_window_total:
        return WindowShare(share, None)
    consuming = sum(1 for cost in costs.values() if cost > 0.0)
    if consuming <= 1:
        return WindowShare(share, None)
    return WindowShare(share, 1.0 / consuming)


def _over_in_window(
    costs: dict[str, float],
    key_id: str,
    tolerance: float,
    *,
    min_window_total: float,
) -> bool:
    window = window_share(costs, key_id, min_window_total=min_window_total)
    return window.fair is not None and window.share > tolerance * window.fair


def key_shares(
    active_key_ids: frozenset[str], long_costs: dict[str, float], fast_costs: dict[str, float]
) -> dict[str, KeyShares]:
    return {
        key_id: KeyShares(long=window_share(long_costs, key_id), fast=window_share(fast_costs, key_id))
        for key_id in active_key_ids
    }


def describe_shares(shares: KeyShares) -> str:
    """Human-readable share summary for logs and the admission 429 body."""

    def _window(label: str, window: WindowShare) -> str:
        if window.fair is None:
            return f"{label} {window.share:.0%} (uncontended)"
        return f"{label} {window.share:.0%} (fair {window.fair:.0%})"

    return f"{_window('7d', shares.long)}, {_window('1h', shares.fast)}"


def classify_over_share(
    *,
    active_key_ids: frozenset[str],
    long_costs: dict[str, float],
    fast_costs: dict[str, float],
    previously_over: frozenset[str],
    enter_tolerance: float = ENTER_TOLERANCE,
    exit_tolerance: float = EXIT_TOLERANCE,
    min_window_total: float = MIN_WINDOW_TOTAL_USD,
) -> frozenset[str]:
    """Pure hysteresis classification of active keys against their fair share.

    Fair share is ``1/k`` per window, ``k`` = keys with positive spend in that
    window (active or not: a ghost's spend still counts in the denominator and
    in ``k``). A key enters the over-share set when its share exceeds
    ``enter_tolerance / k`` in either window, and leaves it only once its share
    is at most ``exit_tolerance / k`` in both windows. Windows with a lone
    consumer or with total spend below ``min_window_total`` classify nobody.
    """
    over: set[str] = set()
    for key_id in active_key_ids:
        tolerance = exit_tolerance if key_id in previously_over else enter_tolerance
        if _over_in_window(long_costs, key_id, tolerance, min_window_total=min_window_total) or _over_in_window(
            fast_costs, key_id, tolerance, min_window_total=min_window_total
        ):
            over.add(key_id)
    return frozenset(over)


async def _active_foreground_key_ids(session: AsyncSession) -> frozenset[str]:
    stmt = select(ApiKey.id).where(
        ApiKey.is_active.is_(True),
        ApiKey.traffic_class == TRAFFIC_CLASS_FOREGROUND,
        or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > utcnow()),
    )
    return frozenset(str(row) for row in (await session.execute(stmt)).scalars())


def _exclude_warmup_clause():
    return RequestLog.request_kind.not_in(WARMUP_REQUEST_KINDS)


async def _raw_cost_by_key(session: AsyncSession, windows) -> dict[str, float]:
    stmt = (
        select(
            RequestLog.api_key_id,
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
        )
        .where(
            RequestLog.api_key_id.is_not(None),
            raw_windows_clause(windows),
            _exclude_warmup_clause(),
        )
        .group_by(RequestLog.api_key_id)
    )
    return {str(row.api_key_id): float(row.cost_usd or 0.0) for row in (await session.execute(stmt)).all()}


async def _long_window_cost_by_key(session: AsyncSession) -> dict[str, float]:
    since = utcnow() - LONG_WINDOW
    folded_costs, raw_windows = await sum_hourly_cost_by_api_key_window(
        session,
        since,
        None,
        filters=(
            RequestUsageHourlyRollup.api_key_id != to_dimension(None),
            RequestUsageHourlyRollup.request_kind.not_in(WARMUP_REQUEST_KINDS),
        ),
    )
    costs: dict[str, float] = {}
    for encoded_key, cost in folded_costs.items():
        key_id = from_dimension(encoded_key)
        if key_id is None:
            continue
        costs[key_id] = costs.get(key_id, 0.0) + cost
    if raw_windows:
        for key_id, cost in (await _raw_cost_by_key(session, raw_windows)).items():
            costs[key_id] = costs.get(key_id, 0.0) + cost
    return costs


async def _fast_window_cost_by_key(session: AsyncSession) -> dict[str, float]:
    since = utcnow() - FAST_WINDOW
    return await _raw_cost_by_key(session, [(since, None)])


class FairShareQuotaClassifier:
    """Per-replica cached over-share classification.

    Lookups never block admission: a stale or missing snapshot schedules a
    single-flight background refresh and the lookup answers from what is
    already known (no degradation while nothing is known yet). A failed
    refresh fails open — degradations are dropped, not extended — and is
    retried after the cache TTL.
    """

    def __init__(self, *, cache_ttl_seconds: float = CACHE_TTL_SECONDS) -> None:
        self._cache_ttl_seconds = cache_ttl_seconds
        self._snapshot: FairShareQuotaSnapshot | None = None
        self._refresh_task: asyncio.Task[None] | None = None

    async def is_over_share(self, api_key_id: str) -> bool:
        snapshot = self._snapshot
        if snapshot is None or time.monotonic() - snapshot.computed_monotonic >= self._cache_ttl_seconds:
            self._schedule_refresh()
        if snapshot is None:
            return False
        return api_key_id in snapshot.over_share_key_ids

    def _schedule_refresh(self) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_task = asyncio.get_running_loop().create_task(self.refresh())

    async def refresh(self) -> None:
        """Recompute the snapshot once (background-task and test entry point)."""
        previous = self._snapshot
        previously_over = previous.over_share_key_ids if previous is not None else frozenset()
        try:
            async with asyncio.timeout(REFRESH_TIMEOUT_SECONDS):
                async with get_background_session() as session:
                    active_key_ids = await _active_foreground_key_ids(session)
                    long_costs = await _long_window_cost_by_key(session)
                    fast_costs = await _fast_window_cost_by_key(session)
        except Exception:
            # Fail open: a broken usage read must never keep foreground keys
            # degraded. Drop degradations; the next scheduled refresh (after
            # the cache TTL) restores classification once reads recover.
            logger.warning("Fair-share quota classification refresh failed; failing open", exc_info=True)
            self._set_snapshot(
                frozenset(),
                previous.active_foreground_key_count if previous is not None else 0,
            )
            return
        over_share = classify_over_share(
            active_key_ids=active_key_ids,
            long_costs=long_costs,
            fast_costs=fast_costs,
            previously_over=previously_over,
        )
        shares = key_shares(active_key_ids, long_costs, fast_costs)
        entered = over_share - previously_over
        left = previously_over - over_share
        if entered or left:
            logger.info(
                "Fair-share quota classification changed entered=%s left=%s over_share=%d active_keys=%d",
                [f"{key_id}[{describe_shares(shares[key_id])}]" for key_id in sorted(entered)],
                sorted(left),
                len(over_share),
                len(active_key_ids),
            )
        self._set_snapshot(over_share, len(active_key_ids), shares=shares)

    def denial_detail(self, api_key_id: str) -> str | None:
        """Share summary for a key's admission denial; ``None`` when unknown."""
        snapshot = self._snapshot
        if snapshot is None:
            return None
        shares = snapshot.shares.get(api_key_id)
        return None if shares is None else describe_shares(shares)

    def reset_if_populated(self) -> None:
        """Drop cached classification and zero the gauge (mode turned off)."""
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = None
        if self._snapshot is None:
            return
        self._snapshot = None
        if PROMETHEUS_AVAILABLE and fair_share_quota_over_share_keys is not None:
            fair_share_quota_over_share_keys.set(0)

    def _set_snapshot(
        self, over_share: frozenset[str], active_key_count: int, *, shares: Mapping[str, KeyShares] | None = None
    ) -> None:
        self._snapshot = FairShareQuotaSnapshot(
            over_share_key_ids=over_share,
            active_foreground_key_count=active_key_count,
            computed_monotonic=time.monotonic(),
            shares=shares or {},
        )
        if PROMETHEUS_AVAILABLE and fair_share_quota_over_share_keys is not None:
            fair_share_quota_over_share_keys.set(len(over_share))


_classifier: FairShareQuotaClassifier | None = None


def get_fair_share_quota_classifier() -> FairShareQuotaClassifier:
    global _classifier
    if _classifier is None:
        _classifier = FairShareQuotaClassifier()
    return _classifier


def reset_fair_share_quota_classifier() -> None:
    """Test hook: drop the process-global classifier and its cache."""
    global _classifier
    _classifier = None


def fair_share_denial_detail(api_key_id: str) -> str | None:
    """Share summary appended to a degraded key's admission 429, so the person
    being throttled sees why without dashboard access."""
    return get_fair_share_quota_classifier().denial_detail(api_key_id)


async def reset_fair_share_quota_classifier_if_mode_disabled() -> None:
    """Settings-invalidation callback (every replica, originator included).

    The admission path resets the classifier when it sees the mode off, but a
    worker that receives no proxy requests would keep its cached verdicts and a
    non-zero livemax gauge indefinitely after the mode is turned off. Only the
    disabled case resets: an unrelated settings save must not drop hysteresis
    state while the mode is on.
    """
    settings = await get_settings_cache().get()
    if not getattr(settings, "fair_share_quota_mode_enabled", False):
        get_fair_share_quota_classifier().reset_if_populated()


async def resolve_effective_traffic_class(
    api_key: ApiKeyData | None,
    *,
    requested: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
    settings: DashboardSettingsData | None = None,
) -> TrafficClass:
    """Admission-time traffic class: static key class, else fair-share verdict.

    Explicitly opportunistic keys stay opportunistic. Foreground keys are
    degraded to ``fair_share_degraded`` only while fair-share quota mode is
    enabled and the key is classified over-share; otherwise the caller's
    requested class passes through unchanged. Callers that already hold the
    dashboard settings pass them in to avoid a second cache lookup.
    """
    if api_key is None:
        return requested
    if api_key.traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC:
        return TRAFFIC_CLASS_OPPORTUNISTIC
    if requested != TRAFFIC_CLASS_FOREGROUND:
        return requested
    if settings is None:
        settings = await get_settings_cache().get()
    if not getattr(settings, "fair_share_quota_mode_enabled", False):
        # Keep the gauge and cached verdicts honest after the mode is turned
        # off; a no-op when nothing is cached.
        get_fair_share_quota_classifier().reset_if_populated()
        return requested
    if await get_fair_share_quota_classifier().is_over_share(api_key.id):
        if PROMETHEUS_AVAILABLE and fair_share_quota_degradations_total is not None:
            fair_share_quota_degradations_total.inc()
        return TRAFFIC_CLASS_FAIR_SHARE_DEGRADED
    return requested
