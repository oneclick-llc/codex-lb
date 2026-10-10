## Why

Codex session affinity keys start with an LF sentinel (`affinity.py`
`_CODEX_SELECTION_KEY_PREFIX`) that keeps the internal key namespace out of
reach of raw client headers. When a request lands on a replica that does not
own its bridge session, the origin forwards it with that key in
`x-codex-bridge-affinity-key`. aiohttp refuses to write the LF, so every such
forward failed with a 500, and with the header safety check the same forward
fails closed with `bridge_forward_invalid`. Either way a thread-keyed request
never reaches its owner.

## What Changes

- An original affinity key that is not a safe raw header value travels as
  URL-safe base64 of its UTF-8 bytes in `x-codex-bridge-affinity-key-b64`,
  and the raw header is not sent. A safe key, including one with an interior
  HTAB, keeps the raw header byte for byte, so owners that predate this
  change keep accepting it.
- The owner accepts only the canonical encoding and uses the decoded key byte
  for byte. Malformed, duplicate or ambiguous affinity key headers fail closed
  with `bridge_forward_invalid`.
- Forward signatures still cover the original key, so the signature format is
  unchanged and a swapped encoded value fails verification.
- The header safety check no longer looks at the affinity key: its wire form,
  raw or base64, is always a safe header value.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: owner forwarding carries affinity keys that are
  unsafe as raw header values in a base64 header instead of failing.
