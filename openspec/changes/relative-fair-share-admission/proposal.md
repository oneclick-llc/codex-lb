## Why

Teams sharing one codex-lb pool need per-user fairness without per-user configuration. Static `ApiKeyLimit` values are absolute token/cost numbers that an operator must recompute and re-enter for every key each time the team grows or shrinks, and hard caps waste idle quota: a light user's unused share cannot flow to heavy users. The existing per-API-key fair share only governs concurrent stream slots (latency), not quota consumption, so today one key can drain the pooled weekly quota for everyone.

## What Changes

- Add an opt-in fair-share quota mode (dashboard setting, default off). When enabled, each active foreground API key is classified by its share of actual pooled consumption (`cost_usd` attributed per key from request usage rollups) among the `k` keys that consumed in a rolling long window plus a short burst window.
- Keys consuming more than `tolerance x 1/k` of a window's pooled consumption are admitted through the opportunistic admission gate with a new `fair_share_degraded` traffic class: every account is gated by the preserve-style weekly pace floor and short-window floor instead of the last-account 5% emergency floor, so an over-share key burns the pool's surplus over linear pace but cannot push it behind pace. Keys under their share, lone consumers, and near-empty windows stay foreground. No requests are newly rejected outright; over-share traffic degrades to pace-floor admission with the existing opportunistic denial envelopes.
- No absolute numbers are configured anywhere: `k` is derived from who actually consumed, so adding a user is just creating a key and idle keys (vacation, CI) never distort anyone's share.
- Explicit `traffic_class: opportunistic` keys, explicit `ApiKeyLimit` rows, and the existing concurrency fair share are unchanged and compose with the new mode.

## Capabilities

### New Capabilities

- None

### Modified Capabilities

- `proxy-admission-control`: new requirement for relative fair-share quota admission (classification, `fair_share_degraded` pace-floor gate, windows, defaults).
- `frontend-architecture`: Settings routing section exposes the fair-share quota mode toggle.
- `database-migrations`: dashboard settings schema gains the fair-share quota mode column with ORM + Alembic coverage.

## Impact

- Backend: dashboard settings ORM/schema/service/API + Alembic migration; a per-key consumption-share classifier (read-only queries over existing hourly usage rollups and the request-log tail, cached in-process); an effective-traffic-class resolution applied at the existing opportunistic admission call sites (HTTP and WebSocket paths).
- Frontend: one toggle in the routing settings section.
- No new tables, no new external endpoints, no changes to upstream account routing or settlement paths.
