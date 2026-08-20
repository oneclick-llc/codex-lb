## Tasks

- [x] Dashboard settings: ORM column `fair_share_quota_mode_enabled` (default false), Alembic migration (upgrade/downgrade), backend settings schemas/service/API plumbing, audit `changed_fields`.
- [x] Consumption-share classifier: per-key `cost_usd` shares over rolling 7d and 1h windows (hourly rollups + request-log tail beyond fold watermark), `N` from active foreground keys, 1.2x degrade / 1.0x restore hysteresis constants, in-process cache with TTL <= 60s.
- [x] Effective-traffic-class resolution (static class else fair-share classification) wired into every opportunistic admission call site: HTTP `_opportunistic_admission_denial` sites and the WebSocket/codex-control parity paths.
- [x] Metrics: degradation counter and current over-share key gauge following existing `codex_lb_*` fair-share metric naming.
- [x] Frontend: routing-section toggle + settings schema/payload plumbing.
- [x] Tests: classifier pure-logic tests (shares, windows, hysteresis, N changes); classifier cache tests (TTL, hysteresis across refreshes, fail-open); migration upgrade/default/downgrade test; admission regression at the route path (default-off unchanged, over-share degraded and denied on closed headroom, admitted on open headroom, under-share unaffected, static opportunistic unchanged). WS/codex-control parity is structural: both paths route through the single `resolve_effective_traffic_class` resolver covered by the resolver tests.
- [x] Change `context.md` rationale note (why relative shares instead of absolute limits) + `docs/routing.md` section with capability link-back. `openspec validate --specs` pending where the CLI exists (noted in context.md).
