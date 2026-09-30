## Why

GPT-6 Sol, GPT-6 Luna, and GPT-6.1 Sol have no pricing entries in this branch,
so their request-log `cost_usd` stays empty and API-key `cost_usd` quotas do
not count their usage. Upstream carries these prices only in the automated
pricing snapshot: GPT-6 Sol and Luna since Soju06/codex-lb#2459, GPT-6.1 Sol in
the open refresh Soju06/codex-lb#2544.

## What Changes

- add canonical standard, Flex, Priority, and long-context prices for
  `gpt-6-sol`, `gpt-6-luna`, and `gpt-6.1-sol` (same values as the upstream
  pricing snapshot)
- add wildcard aliases so suffixed model IDs resolve to the canonical entries
- add regression coverage for resolution and tier-specific cost calculations

## Capabilities

### New Capabilities

- none

### Modified Capabilities

- `api-keys`: cost accounting must recognize GPT-6 Sol, GPT-6 Luna, GPT-6.1 Sol and their suffixed aliases

## Impact

- Code: `app/core/usage/pricing.py`
- Tests: `tests/unit/test_pricing.py`
- No API or database schema changes; costs apply to requests completed after deploy
