# Context: relative-fair-share-admission

## Why relative shares instead of absolute limits

Upstream reports quota only as per-account `used_percent` windows with no
per-key attribution and no stable percent↔token/$ conversion (capacity varies
by plan and over time; see upstream issue #1793). Any absolute per-key budget
therefore needs manual calibration and re-entry every time the team size
changes. codex-lb's own rollups, however, attribute consumption to keys
exactly — so comparing keys **against each other** (share of pooled `cost_usd`
vs `1/k` among the keys that consumed in the window) is both precise and
zero-config: creating a key is the only onboarding step.

`1/k`, not `1/N`: with `N` = all active keys, shares summing to 1 means fewer
than `N/1.2` consumers always leaves someone over-share — one person working
alone would be degraded at 100% share while idle keys "reserved" quota nobody
was going to use. Counting only consumers makes a lone worker, two equal
workers, or three of five equal workers all under-share; 70/30 between two is
over-share for the 70.

## Why degrade instead of reject

Hard caps are not work-conserving: a light user's idle share would go unused.
Routing over-share keys through the opportunistic gate keeps the system
work-conserving, but the static opportunistic floor (5% of the last account,
and effectively nothing while two normal accounts are up) is too weak to
protect anyone. `fair_share_degraded` therefore gates on the **linear pace
line** of each account's long window, with 10 points of slack: an over-share
key is blocked only while remaining% has fallen more than 10pp behind the
share of the week still ahead (below 76% remaining with 6 days left, 33%
with 3, 4% with 1, never in the last hours). A fresh or barely used window
never blocks anybody; the on-pace share stays with the keys it was
over-consuming against, and the reserve melts to zero at reset instead of
being stranded. The 5h window does not gate fair-share admission on `normal` and
`burn_first` accounts — upstream's own 5h exhaustion already binds every
traffic class equally, and a drained 5h window on an idle week must not cut
degraded keys (observed in production). On those accounts the pace line is the
whole gate; `preserve` accounts additionally keep their own floors (weekly
always, short-window whenever upstream reports a 5h reset), because the account
an operator asked to protect must not be the one place an over-share key may
dip. Together with the 1-hour burst window and the
existing concurrency fair share this bounds how much one user can take from
everyone else.

Worked example, one account, no 5h window, 3 days to weekly reset (pace
43% - 10pp slack = 33%): with 30% left a static `opportunistic` key is
admitted (30% > 5%) while a `fair_share_degraded` key is denied with
`429 rate_limit_exceeded` + `Retry-After`; with 40% left it is admitted. One
day before reset (floor 4%) the same key is admitted again at 10% left — the
surplus was going to expire anyway.

## Two fixes that the toggle does not gate

The toggle gates fair-share classification and the `fair_share_degraded`
traffic class, and nothing else. Two changes here are live with the mode off,
because both are bugs in the pre-existing static opportunistic gate that this
work surfaced; a toggle would only hide them from the spec, not from users.

1. **Preserve short-window gating is per reported window.** The preserve gate
   used to fail closed when the account had no short-window reset. That is
   every healthy account now that upstream stopped reporting the 5h window
   (`state.reset_at` is a rate-limit recovery hint, `None` on a healthy
   account), so preserve accounts were walled off from *all* opportunistic burn
   forever — observed as denials on a pool with under 1% of the week consumed.
   The short-window floor now binds only while a short-window reset is actually
   reported; otherwise the weekly floor is the whole preserve gate. Fail-closed
   here protected nothing: an unreported window is not a drained one.
   Consequence with the mode off: static `opportunistic` keys can now burn
   preserve accounts that previously rejected them.
2. **`opportunistic_burn_window_closed` is a local-overload code.** The HTTP
   routes already answered these denials with `429 rate_limit_exceeded`; the
   WebSocket, first-turn, and codex-control selection paths fell through to a
   `503` that Codex surfaces as "unexpected status". Registering the code lines
   those paths up with the HTTP contract and, through the same registry, makes
   the denial account-health-neutral and makes an http-bridge prewarm that hits
   it record `skipped` instead of `error` — all three correct for the same
   reason: a closed burn window is neither the account's fault nor a server
   outage.

Both are stated normatively in the change's `MODIFIED` requirement for
"Opportunistic Proxy Traffic Burns Only Safe Quota". Neither depends on the
fair-share work, so both could ship as a separate PR ahead of it.

## Sync notes

On `/opsx:sync`, fold the "why relative shares" rationale above into
`openspec/specs/proxy-admission-control/context.md` alongside the new
"Relative fair-share quota admission" requirement, and apply the `MODIFIED`
"Opportunistic Proxy Traffic Burns Only Safe Quota" text (preserve short-window
gating + the `opportunistic_burn_window_closed` contract) to the main spec.
`docs/routing.md` already carries the operator-facing description, including
the two toggle-independent fixes, and the capability link-back.

## Verification

`openspec validate --specs` — 57 passed, 0 failed — and
`openspec validate relative-fair-share-admission --strict` (change valid)
were run with @fission-ai/openspec 1.10.0.
