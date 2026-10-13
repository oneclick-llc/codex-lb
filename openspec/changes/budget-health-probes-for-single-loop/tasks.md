## 1. Probe budget

- [x] 1.1 Declare `timeoutSeconds` on the readiness and liveness probes in the chart, above the Kubernetes 1s default and below `periodSeconds * failureThreshold`, with the single-event-loop rationale recorded next to the value

## 2. Single readiness round-trip

- [x] 2.1 Drop the redundant `SELECT 1` from `/health/ready` so the bridge-ring query is the only database round-trip, preserving every existing 503 detail string
- [x] 2.2 Fail readiness when the ring query errors and the bridge readiness gate is disabled, so a database outage cannot report ready

## 3. Tests

- [x] 3.1 The readiness and liveness probes declare a timeout above the Kubernetes default (fails on the pre-fix template, which declares none)
- [x] 3.2 Each probe's timeout stays below its own `periodSeconds * failureThreshold` failure window
- [x] 3.3 Readiness returns 503 when the ring query errors with the bridge gate disabled (returns ready on the pre-fix handler)
