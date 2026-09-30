## ADDED Requirements

### Requirement: GPT-6 Sol, GPT-6 Luna, and GPT-6.1 Sol usage cost pricing is recognized

When computing API-key usage, request-log, reservation, or aggregate cost for these models, the system MUST use these USD-per-1M-token rates for input, cached input, and output:

| Model | Standard | Fast/priority | Flex | Standard long context |
| --- | --- | --- | --- | --- |
| `gpt-6-sol` | `2 / 0.20 / 10` | `4 / 0.40 / 20` | `1 / 0.10 / 5` | `4 / 0.40 / 15` |
| `gpt-6-luna` | `0.10 / 0.01 / 0.50` | `0.20 / 0.02 / 1` | `0.05 / 0.005 / 0.25` | `0.20 / 0.02 / 0.75` |
| `gpt-6.1-sol` | `2 / 0.10 / 10` | `4 / 0.20 / 20` | `1 / 0.05 / 5` | `4 / 0.20 / 15` |

The `priority` and `fast` service-tier aliases MUST use the Fast/priority rates. Standard long-context rates MUST apply only when input tokens exceed 272,000. Flex long-context pricing MUST use the Flex short-context rates and the existing Flex long-context multipliers. Model IDs with a version or snapshot suffix MUST resolve to the matching canonical entry, and `gpt-6.1-sol` MUST NOT resolve to `gpt-6-sol`.

#### Scenario: Standard-tier GPT-6 Sol request uses GPT-6 Sol rates

- **WHEN** a standard-tier `gpt-6-sol` request has 200,000 input tokens, 100,000 cached input tokens, and 1,000,000 output tokens
- **THEN** the computed cost is 10.22 USD

#### Scenario: GPT-6.1 Sol keeps its own cached-input rate

- **WHEN** a standard-tier `gpt-6.1-sol` request has 200,000 input tokens, 100,000 cached input tokens, and 1,000,000 output tokens
- **THEN** the computed cost is 10.21 USD

#### Scenario: Long-context GPT-6 Luna request uses long-context rates

- **WHEN** a standard-tier `gpt-6-luna` request has 300,000 input tokens, 50,000 cached input tokens, and 100,000 output tokens
- **THEN** the computed cost is 0.126 USD

#### Scenario: Suffixed GPT-6 model resolves to its canonical price

- **WHEN** a request completes for `gpt-6-sol-2026-09-20` or `gpt-6.1-sol-2026-09-30`
- **THEN** the system resolves it to the canonical `gpt-6-sol` or `gpt-6.1-sol` pricing entry respectively
