## ADDED Requirements

### Requirement: HTTP probes budget for a single-event-loop replica

The chart's readiness and liveness probes MUST declare an explicit `timeoutSeconds` greater than the Kubernetes default of 1 second, because a replica serves every probe from the same single asyncio event loop that serves proxy traffic and a probe request is scheduled behind whatever the loop is already running. The declared timeout MUST remain below that probe's own `periodSeconds * failureThreshold`, so raising the budget cannot stall detection of a genuinely wedged replica beyond its existing failure window.

#### Scenario: Loop pressure does not evict a serving replica

- **GIVEN** a replica whose event loop is saturated but still serving requests
- **WHEN** the kubelet probes readiness
- **THEN** the probe is allowed more than the 1 second Kubernetes default to be scheduled, so ordinary loop pressure does not drop the pod out of the Service endpoints

#### Scenario: A wedged replica is still detected in its failure window

- **GIVEN** a replica whose probe endpoint never answers
- **WHEN** the kubelet probes it
- **THEN** each attempt expires within `periodSeconds * failureThreshold`, so the probe still fails within the same window it did before the timeout was declared

### Requirement: Readiness makes a single database round-trip

`/health/ready` MUST make exactly one database round-trip per probe. The bridge-ring query is that round-trip and doubles as the database check, so no separate connectivity statement is issued. When the bridge readiness gate is disabled, a ring query error MUST still fail readiness, because the gate's failure detail is the only other place that error would surface.

#### Scenario: A database outage fails readiness with the ring gate disabled

- **GIVEN** the bridge readiness gate is disabled and the ring query fails
- **WHEN** readiness is probed
- **THEN** it responds 503 rather than reporting the replica ready

#### Scenario: Existing readiness failure details are unchanged

- **GIVEN** the bridge readiness gate is enabled and the ring query fails
- **WHEN** readiness is probed
- **THEN** it responds 503 with the bridge ring metadata detail it reported before the round-trip was removed
