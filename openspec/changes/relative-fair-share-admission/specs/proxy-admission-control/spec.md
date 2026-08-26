## ADDED Requirements

### Requirement: Relative fair-share quota admission degrades over-share API keys

When dashboard setting `fair_share_quota_mode_enabled` is enabled, the proxy SHALL classify every active, non-expired foreground API key by its share of pooled consumption among the keys that actually consumed in a window, and SHALL admit requests from over-share keys exclusively through the opportunistic admission gate with the `fair_share_degraded` traffic class (pace-floor headroom only, see below), while under-share keys remain foreground.

Classification SHALL use per-key `cost_usd` attribution from the request usage hourly rollups plus the raw request-log tail beyond the hourly fold watermark, evaluated over a rolling 7-day window and a rolling 1-hour burst window. For each window, `k` SHALL be the number of API keys with positive attributed consumption in that window (idle keys neither count toward `k` nor dilute anyone's share). A key is over-share when its share of the window's pooled consumption exceeds `1.2 x (1/k)` in either window; an over-share key SHALL be restored to foreground when its share returns to at most `1.0 x (1/k)` in both windows. A window with a single consuming key (`k <= 1`) or with total attributed consumption below `0.05 USD` SHALL classify nobody as over-share (a lone consumer has nobody to share with; a near-empty window is noise, not contention). Classification results MAY be cached per replica for at most 60 seconds and SHALL be computed from read-only queries without leader election. Classification lookups SHALL NOT block admission while a refresh is in flight: a stale or missing snapshot serves the last known result (no degradation when nothing is known yet) while a single-flight background refresh runs. A failed refresh SHALL fail open by dropping all degradations until a subsequent refresh succeeds. Turning the mode off SHALL drop cached classification and zero the over-share gauge on every replica through the settings cache-invalidation bus, so a worker that receives no admission requests does not keep reporting stale over-share keys.

Degraded admission SHALL reuse the existing opportunistic gate machinery: the same budget-threshold resolution, the same `429` `rate_limit_exceeded` envelope with `Retry-After` when the burn window is closed, and the same `usage_limit_reached` envelope with `resets_at` when the pool is exhausted. Every denial surface SHALL keep the `429` rate-limit contract of the `opportunistic_burn_window_closed` local-overload code as required by "Opportunistic Proxy Traffic Burns Only Safe Quota". The candidate filter for `fair_share_degraded` traffic SHALL differ from static `opportunistic` traffic in one way: instead of the last-account emergency floor, every `normal` and `burn_first` account SHALL be admitted exactly while its long window is within the pace-line slack (below); `preserve` accounts SHALL require both their preserve floors — unchanged from "Opportunistic Proxy Traffic Burns Only Safe Quota", including the short-window floor whenever a short-window reset is reported — and the pace-line condition, so an account marked preserve is never the one place an over-share key may fall behind pace. The 5h window SHALL NOT gate `fair_share_degraded` traffic on `normal`/`burn_first` accounts: it is upstream's own burst control and binds every traffic class equally once exhausted, and a drained 5h window on an otherwise idle week must not cut degraded keys. An account blocks `fair_share_degraded` traffic only while its long window is more than 10 percentage points behind linear pace: remaining percent at or below the fraction of the window still ahead of `now` minus the slack. Every remaining-percent comparison applied to `fair_share_degraded` traffic — the pace line and the preserve floors alike — SHALL use the window's upstream-reported consumed percent, excluding the selection-ranking pressure (inflight penalty, leased-token pressure) that account state carries for load spreading, so a highly parallel burst on an idle pool does not read as a week burned behind pace; static `opportunistic` traffic SHALL keep its existing pressure-inclusive reading. The pace line SHALL be computed from the long window's upstream-reported length. The slack means a freshly reset or barely used window never blocks anybody — the pool must be measurably overdrawn — while the reserve still shrinks continuously to zero at reset, so no quota is held back all week to be spent or wasted on the last day. The gate binds only while upstream reports a long window with a usable length and reset; an account whose long window, its length, or its reset is unreported SHALL fail open and admit degraded traffic rather than be treated as exhausted. Static `opportunistic` keys SHALL keep the last-account emergency-floor filter, whose preserve inputs gate per reported window as required by "Opportunistic Proxy Traffic Burns Only Safe Quota". Classification SHALL apply at admission time only; in-flight turns SHALL NOT be reclassified.

Observability: the classifier SHALL log at INFO each change of the over-share set (keys entered with their 7-day and 1-hour shares against the fair share, keys left, counts) and SHALL stay silent when a refresh leaves the set unchanged. A `429` `rate_limit_exceeded` denial of `fair_share_degraded` traffic at the HTTP admission gate SHALL append the key's own share summary (7-day and 1-hour share of pooled usage, each with the fair share or "uncontended") to the error message, so the throttled key holder can see why without dashboard access; the summary reflects the denying replica's cached classification.

The mode SHALL NOT alter: explicit `traffic_class: opportunistic` keys (always opportunistic), explicit `ApiKeyLimit` enforcement (unchanged and still binding — classification can never admit a request past a configured hard limit), or the per-API-key concurrent-stream fair share.

The setting SHALL gate exactly two things: fair-share classification and the `fair_share_degraded` traffic class. With the setting disabled (the default) no key SHALL be classified, no request SHALL be admitted with the `fair_share_degraded` traffic class, the classifier SHALL hold no state and SHALL issue no usage queries, and no metric SHALL report a non-zero over-share key count. The setting SHALL NOT gate the preserve short-window condition or the `opportunistic_burn_window_closed` denial contract required by "Opportunistic Proxy Traffic Burns Only Safe Quota": both apply to static `opportunistic` traffic with the setting disabled.

#### Scenario: Mode is off by default

- **WHEN** the dashboard settings row is created for the first time
- **THEN** `fair_share_quota_mode_enabled` is `false`
- **AND** foreground API keys are never degraded to `fair_share_degraded` admission regardless of their consumption share

#### Scenario: Over-share key is degraded to headroom-only admission

- **GIVEN** the mode is enabled, at least two keys consumed in the 7-day window, and an active foreground key's 7-day consumption share exceeds `1.2 x (1/k)`
- **WHEN** that key submits a request
- **THEN** admission is evaluated through the opportunistic admission gate with the `fair_share_degraded` traffic class
- **AND** if no account is above its pace floors the request is denied with the existing `429` `rate_limit_exceeded` envelope and `Retry-After` header
- **AND** if an account is above its pace floors the request is admitted and proceeds through normal account selection

#### Scenario: Denied over-share key is told its share

- **GIVEN** an over-share key whose cached classification shows a 38% 7-day share against a 17% fair share and an uncontended 1-hour window
- **WHEN** its request is denied at the HTTP admission gate
- **THEN** the `429` message ends with `your key's share of pooled usage: 7d 38% (fair 17%), 1h 5% (uncontended)`

#### Scenario: Under-share key is unaffected by pool congestion

- **GIVEN** the mode is enabled and a key's consumption share is at most `1.0 x (1/k)` in both windows
- **WHEN** that key submits a request
- **THEN** the request is admitted as foreground traffic without consulting the opportunistic gate

#### Scenario: Burst is caught by the fast window

- **GIVEN** the mode is enabled and a key's 7-day share is under its fair share
- **WHEN** at least two keys consumed in the 1-hour window and the key's 1-hour consumption share exceeds `1.2 x (1/k)`
- **THEN** the key is classified over-share no later than the classification cache TTL allows
- **AND** its subsequent requests are admitted through the opportunistic gate only

#### Scenario: Lone consumer is never degraded

- **GIVEN** the mode is enabled with five active foreground keys
- **WHEN** only one key consumed anything in the 1-hour window and 7-day consumption is balanced
- **THEN** that key remains foreground (`k = 1` in the burst window; nobody is being under-served)

#### Scenario: Idle keys do not make working keys over-share

- **GIVEN** the mode is enabled with five active foreground keys
- **WHEN** three keys consumed equal amounts this week and two consumed nothing
- **THEN** none of the three is over-share (`1/k = 1/3`, not `1/N = 1/5`)

#### Scenario: Degraded key burns surplus but not the pace reserve

- **GIVEN** an over-share key and a `normal` account with 20% remaining on both windows, on pace
- **WHEN** the key submits a request
- **THEN** it is denied with `429` `rate_limit_exceeded` while a static `opportunistic` key on the same account is still admitted (emergency floor is 5%)
- **AND** on an account within the pace slack (e.g. 1 day left and 10% remaining, where pace says 14% should remain) the same over-share key is admitted — the pool is not measurably overdrawn

#### Scenario: Parallelism is not consumption

- **GIVEN** an over-share key running 30 concurrent streams against an account whose weekly window has 99% actually remaining (the inflight penalty makes the effective percent read 75+ points higher)
- **WHEN** the key submits a request
- **THEN** it is admitted — the pace gate judges the raw weekly percent
- **AND** the same effective percent caused by genuine consumption is denied

#### Scenario: Untouched fresh window never blocks degraded keys

- **GIVEN** an over-share key and an account whose weekly window has just reset with nothing spent
- **WHEN** the key submits a request
- **THEN** it is admitted (the pace line at ~100% minus the slack never exceeds the remaining 100%)

#### Scenario: Weekly pace line is the whole gate

- **GIVEN** an over-share key and a `normal` account reporting a 7-day long window 3 days from its reset (pace line 43% minus 10pp slack = 33%) with 30% remaining
- **WHEN** the key submits a request
- **THEN** it is denied with `429` `rate_limit_exceeded`
- **AND** with 40% weekly remaining the same request is admitted (within the slack)
- **AND** 1 day from reset (pace 14%, slack floor 4%) the same account with 10% remaining admits the request again

#### Scenario: Drained 5h window on an idle week does not cut degraded keys

- **GIVEN** an over-share key and a `normal` account whose 5h window an earlier burst drained to 15% remaining while the weekly window is 99% remaining
- **WHEN** the key submits a request
- **THEN** it is admitted (the 5h window does not gate fair-share admission; upstream's own 5h exhaustion binds all traffic classes equally)

#### Scenario: Selection-path denial keeps the rate-limit envelope

- **GIVEN** an over-share key denied during account selection on the WebSocket or first-turn path
- **WHEN** the denial is mapped to an HTTP response
- **THEN** the status is `429` with `error.type` `rate_limit_error` and code `opportunistic_burn_window_closed`, never `503`

#### Scenario: Unknown usage fails open for degraded keys

- **GIVEN** an over-share key and an account with no reported usage window at all
- **WHEN** the key submits a request
- **THEN** the request is admitted as `fair_share_degraded` traffic (unknown usage is not treated as exhausted)
- **AND** an account that reports a long-window percent but no window length or no reset is admitted too (there is no pace line to judge against)

#### Scenario: Explicitly opportunistic keys are not reclassified

- **GIVEN** the mode is enabled and a key has `traffic_class: opportunistic`
- **WHEN** that key's consumption share is below its fair share
- **THEN** the key remains subject to opportunistic admission (fair-share classification never promotes it to foreground)

#### Scenario: Explicit API key limits remain binding

- **GIVEN** the mode is enabled and a key has an explicit `ApiKeyLimit`
- **WHEN** the key exceeds that limit and its request passes admission
- **THEN** the request is rejected by limit enforcement exactly as with the mode disabled
- **AND** fair-share classification never admits a request past a configured hard limit

#### Scenario: Classification refresh failure fails open

- **GIVEN** the mode is enabled and a key is currently classified over-share
- **WHEN** the classification refresh fails (usage reads are broken or time out)
- **THEN** all fair-share degradations are dropped and the key is admitted as foreground
- **AND** admission is never blocked waiting on the failed or in-flight refresh
- **AND** classification resumes with the next successful refresh

#### Scenario: Idle worker forgets classification when the mode is turned off

- **GIVEN** the mode is enabled and a worker holds a cached classification with over-share keys
- **WHEN** the mode is turned off on any replica and no further admission request reaches that worker
- **THEN** the settings invalidation callback drops the worker's cached classification and zeroes its over-share gauge
- **AND** an unrelated settings change while the mode stays enabled leaves the cached classification intact

#### Scenario: Restored key returns to foreground admission

- **GIVEN** a key was degraded as over-share
- **WHEN** its consumption share returns to at most `1.0 x (1/k)` in both windows
- **THEN** after at most the classification cache TTL its requests are admitted as foreground again

## MODIFIED Requirements

### Requirement: Opportunistic Proxy Traffic Burns Only Safe Quota

When a proxy request is authenticated by an API key whose `traffic_class` is `opportunistic`, the proxy SHALL admit the request only if at least one eligible account can serve opportunistic traffic without crossing the routing policy floors.

Burn-first and normal accounts MAY be drained to zero only when another usable foreground account remains. The last usable normal account SHALL keep an emergency reserve. Preserve accounts SHALL require fresh weekly usage data (weekly percent and weekly reset) and SHALL remain above the dynamic weekly floor. The dynamic 5h floor SHALL bind only while upstream reports a short-window reset for the account — its rate-limit recovery hint, else the 5h window's own reset — and an account with no reported short-window reset SHALL be gated by the weekly floor alone instead of being refused all opportunistic burn.

When account selection fails because no eligible account can serve opportunistic-gate traffic, the failure SHALL carry the stable code `opportunistic_burn_window_closed`, and that code SHALL be registered as a local-overload code. Every surface that renders such a selection failure (WebSocket, first-turn, and codex-control fallbacks included) SHALL map it to HTTP `429` with `error.type` `rate_limit_error` and `Retry-After`, never `503`; it SHALL be account-health-neutral, so the denial SHALL NOT count against the error health of any account; and an HTTP-bridge prewarm that fails with it SHALL be recorded as `skipped`, not as an error. The HTTP admission gate keeps its own `rate_limit_exceeded` envelope unchanged.

Both the short-window gating condition and the `opportunistic_burn_window_closed` contract SHALL apply regardless of `fair_share_quota_mode_enabled`, to static `opportunistic` traffic and to `fair_share_degraded` traffic alike.

#### Scenario: Closed burn window returns OpenAI rate limit
- **WHEN** an opportunistic API key calls a protected Codex-compatible route and no account is currently burnable
- **THEN** the proxy returns HTTP `429`
- **AND** the response uses an OpenAI-style error with code `rate_limit_exceeded`
- **AND** the message begins with `opportunistic burn window closed:`
- **AND** the response includes `Retry-After`

#### Scenario: Preflight admission mirrors routing
- **WHEN** an opportunistic API key calls `/backend-api/codex/opportunistic/admission`
- **THEN** the proxy returns `200` only when the same traffic class could select an account for a real request
- **AND** otherwise returns the same OpenAI-style `429` denial shape

#### Scenario: Preserve account without a reported 5h window gates on its weekly floor

- **GIVEN** `fair_share_quota_mode_enabled` is disabled and a `preserve` account whose 5h window upstream does not report, with 99% of its weekly window remaining
- **WHEN** a static `opportunistic` key submits a request
- **THEN** the request is admitted (the weekly preserve floor is the whole preserve gate)
- **AND** the same account at 10% weekly remaining denies it (the weekly floor still binds)
- **AND** the same account with a reported 5h reset and 5% short-window remaining denies it (the short-window floor binds while it is reported)

#### Scenario: Closed burn window is a rate limit on every surface with the mode disabled

- **GIVEN** `fair_share_quota_mode_enabled` is disabled and a static `opportunistic` key whose burn window is closed
- **WHEN** the denial is raised during account selection on the WebSocket or first-turn path
- **THEN** the status is `429` with `error.type` `rate_limit_error` and code `opportunistic_burn_window_closed`, never `503`
- **AND** no account's error health is degraded by the denial
- **AND** an HTTP-bridge prewarm failing with that code records the prewarm outcome as `skipped`
