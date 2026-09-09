## Why

On 2026-09-09 the dev replica (`looky/codex-lb-0`) pinned its event loop at 1.00 CPU core for 35 minutes (`container_cpu_usage_seconds_total` 0.07 -> 0.996 between 16:15Z and 16:55Z). The process kept serving traffic, but `/health/ready` timed out 157 times (`Readiness probe failed: context deadline exceeded`) and the pod dropped out of the Service endpoints, leaving the ingress with no backend. `/health/live` — a bare `return` with no awaits — never failed once in the same window.

Two chart facts turned loop pressure into an outage:

- The probes declare no `timeoutSeconds`, so Kubernetes applies its 1s default. A replica runs a single asyncio event loop (one worker per replica; `workers_per_instance` greater than 1 is rejected at startup), so a probe request is scheduled behind whatever the loop is already running. One second is not a budget, it is a coin flip.
- `/health/ready` made two database round-trips per probe — `SELECT 1` and then the bridge-ring query — doubling the awaits the probe must be rescheduled for inside that budget. The `SELECT 1` was redundant: the ring query is itself a database round-trip, and its failure already fails readiness.

## What Changes

- The readiness and liveness probes declare an explicit `timeoutSeconds` above the Kubernetes default, still small enough that `timeoutSeconds` stays below `periodSeconds * failureThreshold` so a genuinely wedged replica is detected within the same failure window as before.
- `/health/ready` makes one database round-trip instead of two. The bridge-ring query is the database probe; when the bridge readiness gate is disabled its swallowed error is the only remaining signal that the round-trip failed, so that case now fails readiness explicitly instead of reporting ready.
- No new settings and no new values keys: the probe budget is a chart constant next to the `periodSeconds` and `failureThreshold` it has to stay consistent with.
- The startup probe is untouched: its observed failure mode is `connection refused` before the listener binds, not a timeout.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: the HTTP probes budget for a single-event-loop replica, and readiness makes a single database round-trip.
