## ADDED Requirements

### Requirement: Settings page exposes the fair-share quota mode toggle

The Settings page routing settings section SHALL include a toggle for `fair_share_quota_mode_enabled`, default off, with copy explaining that enabling it degrades API keys consuming more than their fair share of pooled usage to safe-headroom-only admission. The toggle SHALL persist through the existing dashboard settings payload and SHALL NOT require any per-API-key configuration.

#### Scenario: Operator enables fair-share quota mode

- **GIVEN** an operator opens the Settings page and expands the advanced group
- **WHEN** they enable the fair-share quota mode toggle in the routing section and save
- **THEN** the dashboard settings payload persists `fair_share_quota_mode_enabled: true`
- **AND** no per-API-key fields are required to change
