## MODIFIED Requirements

### Requirement: Dashboard settings persistence

The database SHALL persist dashboard settings, including weekly pace working days, the weekly pace gap smoothing window, and the fair-share quota mode toggle.

#### Scenario: Existing installs receive weekly pace smoothing default
- **WHEN** an existing database is migrated
- **THEN** `dashboard_settings.weekly_pace_smoothing_minutes` exists
- **AND** existing rows use a default smoothing window of 30 minutes

#### Scenario: Existing installs receive fair-share quota mode default
- **WHEN** an existing database is migrated to the current head
- **THEN** `dashboard_settings.fair_share_quota_mode_enabled` exists
- **AND** existing rows default it to `false`
- **AND** downgrade removes the column cleanly
