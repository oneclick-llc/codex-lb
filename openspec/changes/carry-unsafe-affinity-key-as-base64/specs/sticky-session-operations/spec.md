## ADDED Requirements

### Requirement: Owner forwarding carries unsafe affinity keys as base64

Owner-forwarded HTTP bridge requests MUST deliver the original affinity key to
the owner byte for byte. A key is safe as a raw header value when every
character is HTAB or in the range 0x20-0x7E and the key has no leading or
trailing whitespace. The origin MUST send a safe key in
`x-codex-bridge-affinity-key` and any other key only in
`x-codex-bridge-affinity-key-b64`, as URL-safe base64 with padding of its
UTF-8 bytes, never both.

#### Scenario: Internal Codex selection key reaches the owner

- **GIVEN** a thread-keyed request whose affinity key starts with the LF
  selection-key sentinel
- **AND** the request lands on a replica that does not own its bridge session
- **WHEN** the origin forwards it to the owner
- **THEN** the key travels in `x-codex-bridge-affinity-key-b64`
- **AND** the owner verifies the signature and sees the same key byte for byte

#### Scenario: Safe key keeps the raw header

- **GIVEN** an affinity key of printable ASCII with an interior HTAB and no
  edge whitespace
- **WHEN** the origin forwards the request
- **THEN** the key travels in `x-codex-bridge-affinity-key` unchanged
- **AND** an owner that predates the base64 header still accepts it

### Requirement: Owner accepts only canonical base64 affinity keys

The owner MUST accept `x-codex-bridge-affinity-key-b64` only in the canonical
URL-safe base64 form with padding, decoding to non-empty valid UTF-8, and MUST
use the decoded key without trimming. A malformed or repeated base64 header,
or a base64 header together with the raw header, MUST fail closed with
`bridge_forward_invalid`. Forward signatures MUST cover the decoded key.

#### Scenario: Non-canonical encoding fails closed

- **GIVEN** a forwarded request whose base64 affinity key decodes to the signed
  key but is not in the canonical form
- **WHEN** the owner parses the forward
- **THEN** it returns `bridge_forward_invalid`

#### Scenario: Swapped encoded key fails closed

- **GIVEN** a forwarded request whose base64 affinity key was replaced with
  the encoding of a different key
- **WHEN** the owner verifies the forward
- **THEN** it returns `bridge_forward_invalid`
