# Design: relative-fair-share-admission

## Context

One codex-lb deployment serves a team through per-user API keys against a shared pool of upstream accounts. Upstream quota is reported only as per-account `used_percent` windows (primary ~5h, secondary weekly) with no per-key attribution and no stable percent-to-token conversion, so a per-key budget cannot be enforced against upstream numbers directly. codex-lb does have exact per-key consumption attribution: `RequestUsageHourlyRollup` (keyed by `api_key_id`, carries `cost_usd`) plus raw `request_logs` for the not-yet-folded tail.

## Goals

- Equal effective share per user with zero per-user configuration; `N` adjusts automatically as keys are created/disabled.
- Work-conserving: idle quota flows to heavy users; when the pool is uncontended nobody is throttled.
- Not gameable: all of a user's consumption counts against the same key; over-share users burn only safe headroom.
- Bounded blast radius of a sudden heavy user (burst window + existing concurrency fair share + protected headroom tail).

## Non-Goals

- No dynamic rewriting of `ApiKeyLimit.max_value` (rejected: needs percent-to-token calibration and a scheduler; the relative scheme avoids both).
- No per-key upstream-percent budgets (upstream data cannot support it).
- No changes to concurrency fair share, sticky sessions, or account routing strategies.

## Decisions

1. **Relative classification, not absolute limits.** A key's consumption share in a window is compared against `1/k`, where `k` = keys with positive consumption in that window. Comparing against `1/N` over all active keys was rejected: shares always sum to 1, so with fewer than `N/1.2` keys consuming somebody is *always* over-share — a lone worker at 3am is "over share" at 100%, and idle/CI keys reserve quota for nobody. Active non-expired foreground keys only bound *who* is classified; they are not the denominator. A lone consumer (`k = 1`) and near-empty windows (`< $0.05`) classify nobody. No absolute numbers exist in config.
2. **Metric: `cost_usd`.** Normalizes across models (tokens of different models are not comparable). Source: `RequestUsageHourlyRollup` summed per `api_key_id` in SQL (watermark-consistent single-statement aggregation — the long window never materializes per-hour rows), plus a `request_logs` tail query beyond the hourly fold watermark.
3. **Two windows.** Long: rolling 7 days (matches the upstream weekly window's order of magnitude). Fast: rolling 1 hour (catches "suddenly ate everything" bursts within minutes instead of days). A key is over-share if it exceeds tolerance in either window.
4. **Tolerance with hysteresis, as code constants.** Degrade when share > 1.2 x (1/N); restore when share <= 1.0 x (1/N). Constants, not settings — no evidence yet that operators need to tune them (simplicity gates).
5. **Enforcement reuses the opportunistic gate with a pace-floor filter.** An effective-traffic-class resolution (static `traffic_class`, else `fair_share_degraded` when the mode is on and the key is over-share) feeds the existing `check_opportunistic_admission` gate, thresholds, denial envelopes (`429 rate_limit_exceeded` + `Retry-After`, `usage_limit_reached` + `resets_at`), and metrics style. The only new branch is in the candidate filter: static `opportunistic` traffic keeps the last-account 5% emergency floor (which with two or more normal accounts is effectively no floor at all — too weak to mean "fair share"), while `fair_share_degraded` traffic is admitted on an account only while that account is **ahead of linear pace** on its long window (remaining% > fraction of the window still ahead) and above the existing preserve short-window floor (20–30% of the 5h window, while upstream reports one). A flat floor was rejected: the preserve weekly floor pins at 25% whenever the account saw foreground traffic in the last 30 minutes — always, in a busy team — which holds a quarter of the week back until the last day and then either burns it in a rush or wastes it. The pace line instead releases quota continuously (86% must remain with 6 days left, 43% with 3, 14% with 1, ~0% at reset): an over-share key burns exactly the surplus the pool is ahead by, never the on-pace share the other keys are consuming, and nothing is left stranded. The window length is inferred from time-to-reset (≤ 7 days → weekly, else monthly plan) because `AccountState` carries no long-window duration. Each window gates independently and only while upstream reports it: with the 5h limit currently removed upstream (`reset_at` absent) the pace line binds alone, and an account with no reported window at all fails open so a fresh account never turns the mode into blanket 429s.
6. **Classification is admission-time only.** In-flight turns and established streams are never reclassified mid-turn; the next admission decision uses the current class.
7. **Per-replica cached classification, no leader election, non-blocking lookups.** Shares are computed from read-only queries and cached in-process (TTL <= 60s). Lookups never wait on a refresh: a stale or missing snapshot schedules a single-flight background refresh and answers from what is already known (no degradation while nothing is known). A failed refresh fails open — degradations are dropped, not extended — and retries after the TTL, so a broken or slow usage read can neither block nor throttle foreground traffic. Replicas may briefly disagree; the consequence is only which admission path a request takes, never double-settlement.
8. **Composition.** Explicit `ApiKeyLimit` enforcement (reserve/settle) is unchanged and binds independently — in the request flow the admission gate runs before limit reservation, and classification can never admit a request past a configured hard limit. Explicit `traffic_class: opportunistic` keys are always opportunistic regardless of share. Existing per-key concurrency fair share operates independently.
9. **Default off.** With the toggle off, behavior is byte-identical to today.

## Risks / Trade-offs

- **Degrade lag**: a burst can consume as foreground until the fast window catches it (bounded by cache TTL + 1h window + concurrency slot caps + the pace-floor reserve that degraded admission cannot cross).
- **Rolling 7d vs upstream weekly reset**: the long window is a rolling 7 days, not aligned to the upstream secondary reset, so consumption from the previous quota week can count against a key's share in the new one for up to a week. Accepted for now; aligning to `secondary_reset_at` is a follow-up if it proves confusing.
- **Cost attribution gaps**: requests with unknown cost fold as zero; acceptable because classification is relative and gaps affect all keys alike.
- **Replica skew**: TTL-bounded divergence between replicas is accepted (see Decision 7).

## Migration Plan

Single Alembic revision adding the boolean settings column (server default false). No data backfill. Downgrade drops the column.

## Open Questions

- None blocking.
