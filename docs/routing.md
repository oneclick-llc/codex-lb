# Routing Strategy Guide

The dashboard setting **Routing strategy** controls how eligible accounts are selected for each request. No strategy can guarantee account-safety outcomes; conservative use still depends on staying within OpenAI terms, using normal request volumes, and avoiding traffic patterns that would be unusual for your accounts.

For low-volume, policy-compliant personal use, start with **Capacity weighted** or **Relative availability** and keep sticky threads enabled. Those strategies preserve session locality while avoiding sudden all-traffic shifts to a single account.

| Routing strategy | Behavior | Trade-offs and recommended use |
|---|---|---|
| Capacity weighted | Prefers accounts with more usable quota headroom. | Good default for mixed pools and normal compliant usage. |
| Relative availability | Draws from the strongest available accounts with configurable weighting. | Smooths distribution while still preferring healthier accounts. |
| Usage weighted | Reacts to observed recent usage. | Useful when usage history should influence selection, but less direct than capacity-based routing. |
| Round robin | Cycles evenly through eligible accounts. | Simple and predictable, but ignores quota shape and reset timing. |
| Fill first | Uses one account heavily before moving on. | Best for controlled drain tests; less conservative for everyday traffic. |
| Sequential drain | Drains accounts in a fixed order. | Useful for maintenance or explicit account rotation, not a normal safety-first default. |
| Reset drain | Prioritizes capacity near reset windows. | Helps consume expiring quota, but can create timing-shaped bursts. |
| Single account | Pins all traffic to one selected active account. | Useful for isolation and debugging; no load balancing. |

Change the strategy live in the dashboard under **Settings → Routing** — no restart required.

## Routing, quotas, and eligibility explainer

### Account eligibility vs displayed status

An account's badge (`Active`, `Paused`, `Limited`, …) is its **displayed status**, derived from the durable account state plus current usage. Eligibility is decided **per request**: the selector can skip an `Active` account because of a cooldown, error backoff, a quota threshold or exhaustion, model/plan incompatibility, or because a thread's continuation state is owned by a different account. `Active` therefore does not mean "will serve the next request".

### Soft sticky routing vs hard Codex continuation affinity

These are two different mechanisms:

- **Soft sticky routing** (the `Sticky threads` toggle and session/thread locality) is a *preference*: keep requests for the same session on the same account when possible, mostly to preserve warm upstream prompt caches. When the preferred account is unavailable or over the sticky thresholds, traffic can move.
- **Hard Codex continuation affinity** binds a request to the account that owns its continuation state — an explicit Codex turn state, a stored `previous_response_id`/conversation, or uploaded file ids. This binding is **not controlled by `Sticky threads`**: turning the toggle off does not make owner-bound requests portable. codex-lb releases the binding only when it can prove the request is a safe, account-neutral replay (or the continuation is migrated).

If a thread's owner account becomes unavailable, requests that still require that owner can fail with `No available accounts` even though the rest of the pool is healthy. Starting a fresh thread (no continuation state) routes normally.

### Primary vs secondary quota, used vs remaining

- **Primary quota** is the short **5-hour** usage window.
- **Secondary quota** is the longer window: **weekly** on most plans, or **monthly** on plans that report only a monthly window (the monthly window is normalized into the secondary slot for routing).

Account pages display each window as **percent remaining**; the sticky reallocation thresholds in Settings are **percent used**. A `Sticky secondary threshold` of `70` means "move sticky sessions off an account once more than 70% of its secondary (weekly or monthly) window has been used" — in quota terms, once less than 30% remains. Note that routing evaluates thresholds against reported usage **plus temporary in-flight pressure** (concurrent requests and leased tokens), so reallocation can begin slightly before the raw account-page numbers reach the threshold.

### Prefer earlier reset

When enabled and several accounts are otherwise eligible, selection is restricted to the accounts whose selected quota window (5h or weekly) resets soonest. Weekly resets are compared in whole-day buckets; when the selected window has no known reset time, the other window is used as a fallback. The preference applies to the `Capacity weighted`, `Usage weighted`, and `Fill first` strategies; the fixed-order and draw-based strategies (`Round robin`, `Relative availability`, `Sequential drain`, `Reset drain`, `Single account`) ignore it.

### Fair-share quota mode

Off by default. When enabled (Settings → Routing → **Fair-share quota mode**), every active foreground API key is classified by its share of the pool's actual cost consumption over a rolling 7-day window plus a 1-hour burst window, compared against the keys that actually consumed in that window. With `k` consuming keys, a key above ~1.2× `1/k` of the window's consumption is temporarily throttled to **pace-line headroom**: it is denied an account only while that account's weekly window has fallen more than 10 percentage points behind linear pace (e.g. below 33% left with 3 days to go, below 4% left with 1 day to go). The 5h window never gates fair-share admission — upstream's own 5h exhaustion binds everyone equally. A fresh or barely used pool never throttles anybody; the on-pace share stays with the keys it was over-consuming against, and because the line falls to zero at reset nothing is held back to be wasted on the last day. It returns to normal once its share falls back to `1/k`. Someone working alone is never throttled (nobody is being under-served), equal consumers are never throttled, and idle keys — vacation, CI — do not count against anyone.

There is nothing to configure per key: adding a team member is just creating a key, and `1/k` adjusts automatically. Explicit per-key limits and `traffic_class: opportunistic` keys keep their existing behavior and compose with the mode: hard limits always bind, and classification can never admit a request past a configured limit. (On HTTP paths the admission gate runs before limit accounting, so an over-share key at a closed burn window sees the admission `429` rather than the limit error.) Over-share keys denied at a closed burn window receive the standard `429 rate_limit_exceeded` response with `Retry-After`, so retrying clients degrade gracefully.

### Limit warm-up

Limit warm-up sends **one small real request** (using the configured warm-up model and prompt) to an opted-in account when one of its quota windows is confirmed to have newly reset, verifying that the account responds. It consumes a small amount of quota. The optional staggered idle mode additionally pre-starts the 5h window of idle opted-in accounts before traffic arrives; the configured cooldown applies to these staggered idle probes, while ordinary reset-confirmed probes fire once per confirmed reset. Accounts opt in individually (`Enable warm-up` in account actions); the last attempt's result, model, and time are shown on the account list entry.

---

*Specs: [account-routing](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/account-routing) · [proxy-admission-control](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/proxy-admission-control) · [frontend-architecture](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/frontend-architecture) · [usage-refresh-policy](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/usage-refresh-policy)*
