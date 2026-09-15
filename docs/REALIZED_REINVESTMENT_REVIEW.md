# Realized reinvestment review v3

`concierge.reinvestment_allocator` turns observed realized-cash unit economics into
an **owner review**, not an autonomous spend instruction.

The predecessor v2 correctly reacquired current GitHub closeout state and canonical
wallet history, but a caller could still author the PR-to-transfer binding and
operator minutes that made real cash look attributable to a particular PR. That
meant a real but unrelated transfer plus a one-minute effort claim could create a
high realized rate and a `SCALE_REVIEW_ELIGIBLE` family.

v3 closes that boundary.

## Authority split

Three evidence classes are deliberately different:

1. **GitHub closeout state** is reacquired live by the allocator core.
2. **Wallet cash existence** is reacquired from canonical settlement history.
3. **Commercial attribution and effort** (`payment_bindings` + `effort_log`) must
   be covered by a fresh detached HMAC attestation from a credential-owning host.

The authority scope binds the exact wallet, closeout-manifest items, payment
bindings, and effort log. It is also bound to a configured provider identity and
principal digest. Any scope edit, signature mismatch, wrong provider/principal,
future timestamp, or capture older than five minutes fails closed **before** the
live provider reads begin.

The public allocator intentionally ships no signing command. A trusted adapter
outside this caller-controlled path must produce the authority object while it
holds the host credential.

## Host configuration

The verifying process requires:

- `REALIZED_REINVESTMENT_HMAC_KEY_HEX` — at least 32 bytes of hex.
- `REALIZED_REINVESTMENT_AUTHORIZED_PROVIDER` — bounded trusted-adapter ID.
- `REALIZED_REINVESTMENT_AUTHORIZED_PRINCIPAL_SHA256` — exact 64-char lowercase
  digest for the authorized principal.

The detached authority object has exact keys:

```json
{
  "schema_version": 1,
  "purpose": "realized-reinvestment-commercial-evidence-authority/v1",
  "provider": "owner-review-host",
  "principal_sha256": "<64 lowercase hex>",
  "captured_at": "2026-09-14T23:58:00Z",
  "scope_sha256": "<64 lowercase hex>",
  "signature_sha256": "<HMAC-SHA256 hex>"
}
```

`signature_sha256` is HMAC-SHA256 over canonical JSON of the object without the
signature field. `scope_sha256` is the SHA-256 of canonical JSON containing the
exact `wallet`, `closeout_manifest_items`, `payment_bindings`, and `effort_log`.

## Compile

```bash
python -m concierge.reinvestment_allocator compile \
  manifest.json bindings.json effort.json evidence-authority.json \
  taxonomy.json policy.json \
  --wallet <canonical-wallet> \
  --capacity-minutes 240 \
  --output reinvestment-review.json
```

The compiler authenticates commercial evidence first, then the preserved v2 core
reacquires live GitHub and wallet evidence and computes realized unit economics.

## Planning inputs are still advisory

Taxonomy, threshold policy, and requested review capacity are intentionally
caller-supplied planning inputs. The host HMAC does **not** pretend they came from
an owner. Therefore a v3 receipt still does not authorize autonomous spend.

The receipt explicitly keeps these authority limits false:

- external contact;
- claim/submission;
- spend;
- payment or wallet mutation;
- accounting/tax conclusion;
- future-revenue claim; and
- guaranteed return.

`SCALE_REVIEW_ELIGIBLE` means only that authenticated commercial evidence passes
the supplied planning threshold and is ready for owner review.

## Verification

`verify-current` reacquires the same live providers and requires a still-fresh
commercial-evidence authority:

```bash
python -m concierge.reinvestment_allocator verify-current \
  reinvestment-review.json manifest.json bindings.json effort.json \
  evidence-authority.json taxonomy.json policy.json \
  --wallet <canonical-wallet> \
  --capacity-minutes 240
```

The outer v3 receipt has its own integrity hash and retains the exact v2 allocator
receipt hash plus a digest of the detached authority. The raw HMAC signature is
not copied into the review receipt.

## Regression boundary

The focused adapter suite covers missing/forged authority, wrong provider or
principal, stale/future authority, every scope mutation (manifest, payment
binding, effort, wallet), authority-key smuggling, core receipt tampering, outer
receipt tampering, current-verification failure, and the original
real-transfer/one-minute forgery. The workflow runs the suite under normal and
optimized Python on supported syntax endpoints.
