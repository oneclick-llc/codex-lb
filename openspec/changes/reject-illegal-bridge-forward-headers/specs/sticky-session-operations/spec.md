## ADDED Requirements

### Requirement: Owner forwarding rejects illegal reconstructed header metadata

Owner-forwarded HTTP bridge requests MUST validate the reconstructed signed
metadata (original affinity kind, downstream turn-state, file-owner,
client-IP, origin/target instance and reservation fields) before building
signatures or posting headers to another owner. If any value contains an
illegal HTTP header control character, the proxy MUST fail closed with the
structured `bridge_forward_invalid` error instead of sending the owner request.

#### Scenario: Unsafe reservation metadata fails closed

- **GIVEN** an owner-forward request carries API-key reservation metadata
- **AND** one reservation field contains an illegal HTTP header control
  character
- **WHEN** the origin builds the owner-forward request
- **THEN** it returns `bridge_forward_invalid`
- **AND** it does not omit only the reservation headers while keeping the owner
  as reservation-settlement authority

### Requirement: Owner forwarding omits unsafe client headers

Ordinary client headers with illegal HTTP control characters MUST be omitted
from the forwarded header map of an owner-forwarded HTTP bridge request.

#### Scenario: Unsafe client header is omitted

- **GIVEN** an owner-forward request includes an ordinary client header with an
  illegal HTTP header control character
- **WHEN** the origin builds the owner-forward request
- **THEN** that client header is not forwarded
- **AND** the signed bridge-forward metadata remains valid
