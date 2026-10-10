## 1. Transport

- [x] 1.1 Send an affinity key that is unsafe as a raw header value in `x-codex-bridge-affinity-key-b64`.
- [x] 1.2 Decode only the canonical encoding on the owner and use the key byte for byte.
- [x] 1.3 Drop the affinity key from the header safety check, since its wire form is always safe.

## 2. Validation

- [x] 2.1 Unit tests: round trip for both signature formats, raw versus base64 choice, a long unsafe key, swapped, malformed and non-canonical encodings of the signed key, ambiguous headers.
- [x] 2.2 Integration tests: a forward over a real socket with the production HTTP protocol, and a thread selection key through the owner route into its bridge session.
- [x] 2.3 Validate this change and reject-illegal-bridge-forward-headers strictly (`openspec validate <change> --type change --strict`, OpenSpec 1.14.1).
