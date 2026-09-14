## Why

GPT-6 Astra has no pricing entry in this branch, so its requests fall through
to no price at all: request-log `cost_usd` stays empty and API-key `cost_usd`
quotas do not count Astra usage. Upstream ships Astra prices only through the
automated pricing snapshot (Soju06/codex-lb#2298), which this branch does not
carry yet.

## What Changes

- add the canonical `gpt-6-astra` standard, Flex, Priority, and long-context
  prices published on 2026-09-10 (same values as the upstream pricing snapshot)
- add a wildcard alias so suffixed Astra model IDs resolve to the canonical entry
- add regression coverage for resolution and tier-specific cost calculations

## Capabilities

### New Capabilities

- none

### Modified Capabilities

- `api-keys`: cost accounting must recognize GPT-6 Astra and its suffixed aliases

## Impact

- Code: `app/core/usage/pricing.py`
- Tests: `tests/unit/test_pricing.py`
- No API or database schema changes
