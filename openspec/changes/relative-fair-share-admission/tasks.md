## Tasks

- [ ] Dashboard settings: ORM column `fair_share_quota_mode_enabled` (default false), Alembic migration (upgrade/downgrade), backend settings schemas/service/API plumbing, audit `changed_fields`.
- [ ] Consumption-share classifier: per-key `cost_usd` shares over rolling 7d and 1h windows (hourly rollups + request-log tail beyond fold watermark), `N` from active foreground keys, 1.2x degrade / 1.0x restore hysteresis constants, in-process cache with TTL <= 60s.
- [ ] Effective-traffic-class resolution (static class else fair-share classification) wired into every opportunistic admission call site: HTTP `_opportunistic_admission_denial` sites and the WebSocket/codex-control parity paths.
- [ ] Metrics: degradation counter and current over-share key gauge following existing `codex_lb_*` fair-share metric naming.
- [ ] Frontend: routing-section toggle + settings schema/payload plumbing.
- [ ] Tests: classifier pure-logic tests (shares, windows, hysteresis, N changes); admission regression at the route path (default-off unchanged, over-share degraded and denied on closed headroom, admitted on open headroom, under-share unaffected, static opportunistic unchanged, ApiKeyLimit precedence); WebSocket-path parity test.
- [ ] OpenSpec validation, capability `context.md` note (why relative shares instead of absolute limits), docs page link-back.
