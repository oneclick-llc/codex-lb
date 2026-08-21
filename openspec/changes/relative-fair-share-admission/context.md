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
line** of each account's long window: an over-share key is admitted only
while remaining% exceeds the share of the week still ahead (86% with 6 days
left, 43% with 3, 14% with 1, ~0% at reset). It burns the surplus the pool
is ahead by and nothing more; the on-pace share stays with the keys it was
over-consuming against, and the reserve melts to zero at reset instead of
being stranded. The 5h window, while upstream reports one, keeps the preserve
short floor (20–30%). Windows gate only while upstream reports them — with
the 5h limit removed upstream, the pace line is the whole gate. Together with the 1-hour burst window and the
existing concurrency fair share this bounds how much one user can take from
everyone else.

Worked example, one account, no 5h window, 3 days to weekly reset (pace:
43% should remain): with 40% left a static `opportunistic` key is admitted
(40% > 5%) while a `fair_share_degraded` key is denied with
`429 rate_limit_exceeded` + `Retry-After`; with 50% left it is admitted. One
day before reset (pace: 14%) the same key is admitted again at 20% left —
the surplus was going to expire anyway.

## Sync notes

On `/opsx:sync`, fold the "why relative shares" rationale above into
`openspec/specs/proxy-admission-control/context.md` alongside the new
"Relative fair-share quota admission" requirement. `docs/routing.md` already
carries the operator-facing description and the capability link-back.

## Verification

`openspec validate --specs` — 57 passed, 0 failed — and
`openspec validate relative-fair-share-admission --strict` (change valid)
were run with @fission-ai/openspec 1.10.0.
