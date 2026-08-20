# Context: relative-fair-share-admission

## Why relative shares instead of absolute limits

Upstream reports quota only as per-account `used_percent` windows with no
per-key attribution and no stable percent↔token/$ conversion (capacity varies
by plan and over time; see upstream issue #1793). Any absolute per-key budget
therefore needs manual calibration and re-entry every time the team size
changes. codex-lb's own rollups, however, attribute consumption to keys
exactly — so comparing keys **against each other** (share of pooled `cost_usd`
vs `1/N`) is both precise and zero-config: creating a key is the only
onboarding step.

## Why degrade instead of reject

Hard caps are not work-conserving: a light user's idle share would go unused.
Routing over-share keys through the existing opportunistic gate keeps the
system work-conserving (idle headroom is burned) while the gate's protected
headroom tail plus the 1-hour burst window plus the existing concurrency fair
share bound how much one user can take from everyone else.

## Sync notes

On `/opsx:sync`, fold the "why relative shares" rationale above into
`openspec/specs/proxy-admission-control/context.md` alongside the new
"Relative fair-share quota admission" requirement. `docs/routing.md` already
carries the operator-facing description and the capability link-back.

## Verification

`openspec validate --specs` — 57 passed, 0 failed — and
`openspec validate relative-fair-share-admission --strict` (change valid)
were run with @fission-ai/openspec 1.10.0.
