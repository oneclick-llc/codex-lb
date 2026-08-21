## ADDED Requirements

### Requirement: Relative fair-share quota admission degrades over-share API keys

When dashboard setting `fair_share_quota_mode_enabled` is enabled, the proxy SHALL classify every active, non-expired foreground API key by its share of pooled consumption among the keys that actually consumed in a window, and SHALL admit requests from over-share keys exclusively through the opportunistic admission gate with the `fair_share_degraded` traffic class (pace-floor headroom only, see below), while under-share keys remain foreground.

Classification SHALL use per-key `cost_usd` attribution from the request usage hourly rollups plus the raw request-log tail beyond the hourly fold watermark, evaluated over a rolling 7-day window and a rolling 1-hour burst window. For each window, `k` SHALL be the number of API keys with positive attributed consumption in that window (idle keys neither count toward `k` nor dilute anyone's share). A key is over-share when its share of the window's pooled consumption exceeds `1.2 x (1/k)` in either window; an over-share key SHALL be restored to foreground when its share returns to at most `1.0 x (1/k)` in both windows. A window with a single consuming key (`k <= 1`) or with total attributed consumption below `0.05 USD` SHALL classify nobody as over-share (a lone consumer has nobody to share with; a near-empty window is noise, not contention). Classification results MAY be cached per replica for at most 60 seconds and SHALL be computed from read-only queries without leader election. Classification lookups SHALL NOT block admission while a refresh is in flight: a stale or missing snapshot serves the last known result (no degradation when nothing is known yet) while a single-flight background refresh runs. A failed refresh SHALL fail open by dropping all degradations until a subsequent refresh succeeds.

Degraded admission SHALL reuse the existing opportunistic gate machinery: the same budget-threshold resolution, the same `429` `rate_limit_exceeded` envelope with `Retry-After` when the burn window is closed, and the same `usage_limit_reached` envelope with `resets_at` when the pool is exhausted. The candidate filter for `fair_share_degraded` traffic SHALL differ from static `opportunistic` traffic in one way: instead of the last-account emergency floor, every `normal` and `burn_first` account SHALL be admitted only while it is ahead of linear pace on its long window and above the short-window floor that `preserve` accounts apply to opportunistic burn; `preserve` accounts keep their existing floors. "Ahead of linear pace" means the long window's remaining percent exceeds the fraction of the window still ahead of `now` (window length inferred from time-to-reset: at most 7 days → weekly, otherwise a monthly plan window). The reserve therefore shrinks continuously to zero at reset: an over-share key may burn only surplus over pace, and no quota is held back all week to be spent or wasted on the last day. Each window SHALL gate independently and only while upstream reports it (an absent 5h window — `reset_at` unknown — leaves the long-window pace line binding alone; an account with no reported window fails open and admits degraded traffic rather than being treated as exhausted). Static `opportunistic` keys SHALL keep the existing emergency-floor filter unchanged. Classification SHALL apply at admission time only; in-flight turns SHALL NOT be reclassified.

The mode SHALL NOT alter: explicit `traffic_class: opportunistic` keys (always opportunistic), explicit `ApiKeyLimit` enforcement (unchanged and still binding — classification can never admit a request past a configured hard limit), or the per-API-key concurrent-stream fair share. With the setting disabled (the default), admission behavior SHALL be identical to the mode not existing.

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
- **AND** on an account ahead of linear weekly pace (e.g. 1 day left and 20% remaining, where pace says 14% should remain) the same over-share key is admitted, because only the surplus over pace is burnable

#### Scenario: Weekly floor binds while upstream reports no 5h window

- **GIVEN** an over-share key and a `normal` account whose 5h window is not reported (`reset_at` unknown), 3 days from its weekly reset (pace: 43% should remain) and with 40% remaining
- **WHEN** the key submits a request
- **THEN** it is denied with `429` `rate_limit_exceeded` (the weekly pace line gates alone)
- **AND** with 50% weekly remaining the same request is admitted
- **AND** 1 day from reset (pace: 14%) the same account with 20% remaining admits the request again

#### Scenario: Unknown usage fails open for degraded keys

- **GIVEN** an over-share key and an account with no reported usage window at all
- **WHEN** the key submits a request
- **THEN** the request is admitted as `fair_share_degraded` traffic (unknown usage is not treated as exhausted)

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

#### Scenario: Restored key returns to foreground admission

- **GIVEN** a key was degraded as over-share
- **WHEN** its consumption share returns to at most `1.0 x (1/k)` in both windows
- **THEN** after at most the classification cache TTL its requests are admitted as foreground again
