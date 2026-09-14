## ADDED Requirements

### Requirement: GPT-6 Astra usage cost pricing is recognized

When computing API-key usage, request-log, reservation, or aggregate cost for `gpt-6-astra`, the system MUST use these USD-per-1M-token rates for input, cached input, and output:

| Model | Standard | Fast/priority | Flex | Standard long context |
| --- | --- | --- | --- | --- |
| `gpt-6-astra` | `10 / 1 / 50` | `20 / 2 / 100` | `5 / 0.50 / 25` | `20 / 2 / 75` |

The `priority` and `fast` service-tier aliases MUST use the Fast/priority rates. Standard long-context rates MUST apply only when input tokens exceed 272,000. Flex long-context pricing MUST use the Flex short-context rates and the existing Flex long-context multipliers. Model IDs with a version or snapshot suffix MUST resolve to the canonical `gpt-6-astra` entry.

#### Scenario: Standard-tier Astra request uses Astra rates

- **WHEN** a standard-tier `gpt-6-astra` request has 200,000 input tokens, 100,000 cached input tokens, and 1,000,000 output tokens
- **THEN** the computed cost is 51.10 USD

#### Scenario: Long-context Astra request uses long-context rates

- **WHEN** a standard-tier `gpt-6-astra` request has 300,000 input tokens, 50,000 cached input tokens, and 100,000 output tokens
- **THEN** the computed cost is 12.60 USD

#### Scenario: Suffixed Astra model resolves to the canonical price

- **WHEN** a request completes for `gpt-6-astra-2026-09-10`
- **THEN** the system resolves it to the canonical `gpt-6-astra` pricing entry
