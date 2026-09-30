# Upstream issue draft (not posted)

Proposal issue for Soju06/codex-lb, to be referenced by the implementation PR as `Fixes #N`.

---

**Title:** feat(proxy): relative fair-share quota admission — degrade over-share API keys to safe-headroom-only admission

### Pre-flight checklist

- [x] I searched existing issues and discussions for similar proposals.
- [x] This is a concrete proposal, not an open-ended question.

### Problem / motivation

Teams share one codex-lb pool through per-user API keys. Today nothing bounds one key's share of pooled **quota consumption**:

- `ApiKeyLimit` is an absolute token/$ cap per key. For "everyone gets an equal share" the operator must compute `pool / N` by hand and re-enter it on every key each time the team grows or shrinks. Hard caps are also not work-conserving: a light user's idle share cannot flow to heavy users.
- #1535 / #1536 added per-API-key fair share for **concurrent stream slots**, which protects latency under congestion — but not quota. A single key can still burn the pool's 5h/weekly windows for everyone (the quota-consumption variant of "one bursty key starves all others").
- `traffic_class: opportunistic` requires routing a user's overflow through a second key, which is both manual and gameable: opportunistic usage is not attributed to the user's personal share, so a savvy user runs opportunistic all day and keeps their untouched foreground share in reserve.

### Proposed change

An opt-in fair-share quota mode (single dashboard toggle, default off). When enabled:

- Every active foreground API key is classified by its share of **actual pooled consumption**: `cost_usd` attributed per key from the existing request-usage hourly rollups (plus the raw request-log tail beyond the fold watermark), over a rolling 7-day window and a rolling 1-hour burst window.
- A key is over-share when its share exceeds `1.2 × (1/k)` in either window, where `k` is the number of keys that **actually consumed** in that window — not the number of active keys. It is restored at `≤ 1.0 × (1/k)`. Two exemptions keep the rule from firing on nothing: a window with a single consumer classifies nobody (a lone worker has no one to share with — against `1/N` they would read as 100% over-share), and so does a window whose total attributed spend is below a small noise floor (`$0.05`). Constants, not settings.
- Over-share keys' requests are admitted through the **existing opportunistic admission gate** with a distinct traffic class, so they burn only quota the pool is ahead on: an account is blocked for them only while its long (weekly/monthly) window has fallen more than 10 percentage points behind linear pace, rather than by the gate's last-account emergency floor. Denial envelopes are the existing ones (`429 rate_limit_exceeded` + `Retry-After` when the burn window is closed, `usage_limit_reached` + `resets_at` when the pool is exhausted). Under-share keys remain foreground. No requests are newly hard-rejected.
- Classification is admission-time only, computed from read-only queries, cached per replica (TTL ≤ 60s), no leader election.
- Explicit `ApiKeyLimit` rows (evaluated first), explicit `traffic_class: opportunistic` keys, and the #1536 concurrency fair share are unchanged and compose.

Resulting properties:

- **Zero per-user configuration.** Adding a user = creating a key; `1/k` adjusts itself. No absolute numbers anywhere.
- **Work-conserving.** While the pool is uncontended, nobody is throttled; idle quota flows to heavy users via safe headroom. Keys that consume nothing (vacation, CI) neither reserve quota nor inflate anyone's share.
- **Not gameable.** All of a user's consumption counts against one key.
- **Bounded blast radius.** One user cannot drain the pool: an over-share key never takes an account past the pace line, the 1h window catches bursts within minutes, and #1536 slot caps bound burn rate.

### Alternatives considered

- Static equal `ApiKeyLimit`s — manual re-entry on every team-size change; wastes idle quota.
- Overcommitted caps (`m × pool/N` for some overcommit factor `m`) — same maintenance burden, only partial utilization of idle quota.
- Comparing each key against `1/N` over all active keys — rejected: shares always sum to 1, so with fewer than `N/1.2` keys consuming, somebody is *always* over-share. One person working at night would be throttled at 100% share while idle keys reserved quota for nobody. Hence `1/k` over actual consumers, plus the lone-consumer and noise-floor exemptions.
- Soft limits (degrade-to-opportunistic on exceeding an absolute `ApiKeyLimit`) — still needs the absolute calibration that upstream data cannot provide: `used_percent` is per account with no stable percent↔token/$ conversion (cf. #1793).
- A scheduler dynamically rewriting `max_value` — heavier machinery with the same calibration problem.

### Area

Proxy / admission control, plus one dashboard settings toggle.

### Additional context

Builds directly on the #1536 opportunistic/fair-share machinery — the classification result feeds the existing gate; no new admission paths, tables, or endpoints. Orthogonal to #1528 (per-account caps reserve quota from the pool; this distributes the pool among keys).

The mode itself is off by default: with the toggle off no key is classified and no request takes the degraded path. Two fixes to the *existing* opportunistic gate that this work uncovered do apply regardless of the toggle, and can ship separately: (1) a `preserve` account for which upstream reports no 5h window is gated by its weekly floor alone instead of failing closed and being refused all opportunistic burn — which is every healthy account now that the 5h window is not reported; (2) `opportunistic_burn_window_closed` becomes a local-overload code, so the WebSocket/first-turn/codex-control selection paths return a `429` rate-limit error, matching what the HTTP routes already returned, instead of a `503` that Codex reports as "unexpected status", the denial stays account-health-neutral, and an http-bridge prewarm that hits it counts as skipped rather than failed.

I have an OpenSpec change (proposal, design, spec deltas, tasks) ready and can follow up with the implementation PR.
