## ADDED Requirements

### Requirement: Relative fair-share quota admission degrades over-share API keys

When dashboard setting `fair_share_quota_mode_enabled` is enabled, the proxy SHALL classify every active, non-expired foreground API key by its share of pooled consumption and SHALL admit requests from over-share keys exclusively through the existing opportunistic admission path (safe quota headroom only), while under-share keys remain foreground.

Classification SHALL use per-key `cost_usd` attribution from the request usage hourly rollups plus the raw request-log tail beyond the hourly fold watermark, evaluated over a rolling 7-day window and a rolling 1-hour burst window. A key is over-share when its share of pooled consumption exceeds `1.2 x (1/N)` in either window, where `N` is the count of active, non-expired foreground API keys; an over-share key SHALL be restored to foreground when its share returns to at most `1.0 x (1/N)` in both windows. Classification results MAY be cached per replica for at most 60 seconds and SHALL be computed from read-only queries without leader election. Classification lookups SHALL NOT block admission while a refresh is in flight: a stale or missing snapshot serves the last known result (no degradation when nothing is known yet) while a single-flight background refresh runs. A failed refresh SHALL fail open by dropping all degradations until a subsequent refresh succeeds.

Degraded admission SHALL reuse the existing opportunistic gate semantics unchanged: the same budget-threshold resolution, the same `429` `rate_limit_exceeded` envelope with `Retry-After` when the burn window is closed, and the same `usage_limit_reached` envelope with `resets_at` when the pool is exhausted. Classification SHALL apply at admission time only; in-flight turns SHALL NOT be reclassified.

The mode SHALL NOT alter: explicit `traffic_class: opportunistic` keys (always opportunistic), explicit `ApiKeyLimit` enforcement (unchanged and still binding — classification can never admit a request past a configured hard limit), or the per-API-key concurrent-stream fair share. With the setting disabled (the default), admission behavior SHALL be identical to the mode not existing.

#### Scenario: Mode is off by default

- **WHEN** the dashboard settings row is created for the first time
- **THEN** `fair_share_quota_mode_enabled` is `false`
- **AND** foreground API keys are never degraded to opportunistic admission regardless of their consumption share

#### Scenario: Over-share key is degraded to headroom-only admission

- **GIVEN** the mode is enabled and an active foreground key's 7-day consumption share exceeds `1.2 x (1/N)`
- **WHEN** that key submits a request
- **THEN** admission is evaluated through the opportunistic admission gate
- **AND** if no account has safe headroom the request is denied with the existing `429` `rate_limit_exceeded` envelope and `Retry-After` header
- **AND** if safe headroom exists the request is admitted and proceeds through normal account selection

#### Scenario: Under-share key is unaffected by pool congestion

- **GIVEN** the mode is enabled and a key's consumption share is at most `1.0 x (1/N)` in both windows
- **WHEN** that key submits a request
- **THEN** the request is admitted as foreground traffic without consulting the opportunistic gate

#### Scenario: Burst is caught by the fast window

- **GIVEN** the mode is enabled and a key's 7-day share is under its fair share
- **WHEN** the key's 1-hour consumption share exceeds `1.2 x (1/N)`
- **THEN** the key is classified over-share no later than the classification cache TTL allows
- **AND** its subsequent requests are admitted through the opportunistic gate only

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
- **WHEN** its consumption share returns to at most `1.0 x (1/N)` in both windows
- **THEN** after at most the classification cache TTL its requests are admitted as foreground again
