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
- A key is over-share when its share exceeds `1.2 × (1/N)` in either window (`N` = active foreground keys); it is restored at `≤ 1.0 × (1/N)`. Constants, not settings.
- Over-share keys' requests are admitted through the **existing opportunistic admission gate** (safe quota headroom only), with the same thresholds and denial envelopes (`429 rate_limit_exceeded` + `Retry-After` when the burn window is closed, `usage_limit_reached` + `resets_at` when the pool is exhausted). Under-share keys remain foreground. No requests are newly hard-rejected.
- Classification is admission-time only, computed from read-only queries, cached per replica (TTL ≤ 60s), no leader election.
- Explicit `ApiKeyLimit` rows (evaluated first), explicit `traffic_class: opportunistic` keys, and the #1536 concurrency fair share are unchanged and compose.

Resulting properties:

- **Zero per-user configuration.** Adding a user = creating a key; `1/N` adjusts itself. No absolute numbers anywhere.
- **Work-conserving.** While the pool is uncontended, nobody is throttled; idle quota flows to heavy users via safe headroom.
- **Not gameable.** All of a user's consumption counts against one key.
- **Bounded blast radius.** One user cannot drain the pool: the opportunistic gate's protected headroom tail is foreground-only, the 1h window catches bursts within minutes, and #1536 slot caps bound burn rate.

### Alternatives considered

- Static equal `ApiKeyLimit`s — manual re-entry on every team-size change; wastes idle quota.
- Overcommitted caps (`k × pool/N`) — same maintenance burden, only partial utilization of idle quota.
- Soft limits (degrade-to-opportunistic on exceeding an absolute `ApiKeyLimit`) — still needs the absolute calibration that upstream data cannot provide: `used_percent` is per account with no stable percent↔token/$ conversion (cf. #1793).
- A scheduler dynamically rewriting `max_value` — heavier machinery with the same calibration problem.

### Area

Proxy / admission control, plus one dashboard settings toggle.

### Additional context

Builds directly on the #1536 opportunistic/fair-share machinery — the classification result feeds the existing gate; no new admission paths, tables, or endpoints. Orthogonal to #1528 (per-account caps reserve quota from the pool; this distributes the pool among keys). Defaults off; behavior with the toggle off is identical to today. I have an OpenSpec change (proposal, design, spec deltas, tasks) ready and can follow up with the implementation PR.
