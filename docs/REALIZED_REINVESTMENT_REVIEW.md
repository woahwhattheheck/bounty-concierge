# Externally signed realized-cash reinvestment review

`concierge.reinvestment_allocator` prepares an advisory owner review from live
GitHub closeout state, canonical wallet history, exact payment-to-PR bindings,
and operator effort. It does **not** treat a transfer's existence as proof that
the transfer paid a selected pull request.

The production surface is fail-closed:

1. A credential-owning authority outside the review process signs the exact
   closeout manifest, payment bindings, effort log, and wallet identity.
2. The public module verifies the checked-in API and transport source as pinned
   Git blobs, executes those exact bytes in private namespaces, and injects the
   pinned worker-invoker factory into the pinned API factory before exporting any
   public callable. Importable helper module objects are not an authority source.
3. The resulting public closure starts a fresh `python -I` worker through
   boot-captured POSIX primitives. The worker is executable-only and cannot be
   imported as a reusable in-process authority module.
4. The worker verifies the boot-pinned RSA public key, authority identity,
   exact scope, signature, and five-minute freshness window **before** loading
   any provider or allocator code.
5. Only then does the worker reacquire current GitHub closeout state and
   canonical wallet history and compile realized economics.
6. The returned v4 receipt includes the complete detached-signature envelope,
   the pinned allocator-core Git blob identity, and an explicit authority
   ceiling.

A receipt is an input to human review. It is **not an authorization to spend**,
contact a sponsor, submit a claim, mutate a payment or wallet, recognize
accounting revenue, make a tax conclusion, or promise future returns.

## Host configuration

Set these variables before importing `concierge.reinvestment_allocator` or
starting its CLI process:

| Variable | Meaning |
|---|---|
| `REALIZED_REINVESTMENT_AUTHORITY_RSA_MODULUS_HEX` | Canonical lowercase RSA modulus, 2048–8192 bits. The private exponent must remain outside the review process. |
| `REALIZED_REINVESTMENT_AUTHORITY_RSA_KEY_ID` | Bounded identifier for the active signing key. |
| `REALIZED_REINVESTMENT_AUTHORIZED_PROVIDER` | Bounded identifier for the credential-owning evidence provider. |
| `REALIZED_REINVESTMENT_AUTHORIZED_PRINCIPAL_SHA256` | Lowercase SHA-256 identity of the authorized principal. |

The public surface snapshots its launch environment once when
`concierge.reinvestment_allocator` is imported. Changing these variables later
does not rotate the trust root. Restart the trusted host process for an
intentional key or provider change.

The worker receives no symmetric signing secret. It carries only the public RSA
modulus and fixed public exponent `65537`; therefore ordinary review execution
cannot mint new authority attestations.

## Signed authority envelope

The authority object has exactly these fields:

```json
{
  "schema_version": 2,
  "purpose": "realized-reinvestment-commercial-evidence-authority/v2",
  "algorithm": "rsa-pkcs1v15-sha256",
  "key_id": "owner-rsa-2026-09",
  "public_key_sha256": "<64 lowercase hex>",
  "provider": "owner-review-host",
  "principal_sha256": "<64 lowercase hex>",
  "captured_at": "2026-09-15T12:34:56Z",
  "scope_sha256": "<64 lowercase hex>",
  "signature_hex": "<modulus-width lowercase hex>"
}
```

`commercial_evidence_scope_sha256(...)` computes `scope_sha256` over canonical
JSON containing:

```json
{
  "wallet": "<wallet>",
  "closeout_manifest_items": [],
  "payment_bindings": [],
  "effort_log": {}
}
```

The public-key fingerprint is:

```text
SHA256(minimal_big_endian_modulus || 0x00 || ASCII("65537"))
```

The external signer signs the authority object **without** `signature_hex` using
strict RSA PKCS#1 v1.5 SHA-256 over:

```text
UTF8("realized-reinvestment-commercial-evidence-authority/v2") || 0x00 || canonical_json(authority_core)
```

Canonical JSON is UTF-8, sorted by key, compact separators, no NaN or infinity,
and no duplicate object keys. `captured_at` must be canonical whole-second UTC
and no more than 300 seconds old when the isolated worker verifies it.

## CLI

Compile a new advisory receipt:

```bash
python -m concierge.reinvestment_allocator compile \
  manifest.json bindings.json effort.json authority.json \
  taxonomy.json policy.json \
  --wallet "$WALLET" --capacity-minutes 240 \
  --output review.json
```

Reacquire current provider state and verify an existing receipt:

```bash
python -m concierge.reinvestment_allocator verify-current \
  review.json manifest.json bindings.json effort.json authority.json \
  taxonomy.json policy.json \
  --wallet "$WALLET" --capacity-minutes 240
```

The CLI refuses unsafe output replacement, duplicate JSON keys, non-UTF-8 JSON,
non-finite numbers, multiple stdin inputs, malformed authority, stale authority,
provider drift, allocator-core drift, bootstrap-helper drift, and non-POSIX
execution.

## Receipt verification semantics

`verify_reinvestment_receipt_current(...)` starts a new isolated worker,
re-verifies the detached signature, reacquires current GitHub and wallet state,
and compares the full canonical receipt.

`verify_receipt_integrity_only(...)` checks only the receipt's self-digest. It
does **not** prove that the receipt is current, that the signer is authorized,
or that any provider observation remains valid.

The receipt embeds the complete authority envelope so downstream systems can
independently pin `key_id`, `public_key_sha256`, provider, and principal instead
of trusting a mutable label emitted by the producing process.

## Isolation and trust boundary

The parent public API never imports the allocator core or provider modules. All
signature verification and receipt-minting logic lives in the single
executable-only worker; there are no importable worker authority/runtime helper
modules.

The parent bootstrap also does not trust the importable
`concierge._reinvestment_allocator_api` or
`concierge._reinvestment_allocator_transport` module objects. It reads those
sibling source files directly, bounds their size, verifies their exact Git-blob
SHA-1 identities, decodes strict UTF-8, and executes the verified bytes in
private namespaces. The source-pinned transport factory is injected explicitly
into the source-pinned API factory, which then captures the worker path,
interpreter path, process primitives, limits, protocol, and boot environment.
Normal pre-import mutation of the importable helper modules, or fake helper
objects pre-seeded in `sys.modules`, therefore cannot become the public
reinvestment authority graph.

The worker runs under `python -I`, inserts only its own repository root, verifies
the external authority first, and then loads the reviewed allocator
implementation from `_reinvestment_allocator_core.source` only when its decoded
Git blob SHA-1 is exactly `5731fd0652bc94b20d1f3b2de48628f633f550a9`.

That `.source` file is a non-importable implementation resource, not a supported
public authority entrypoint. Any legacy v2 output lacks the v4 detached-signature
envelope and is rejected by the v4 current-verification surface.

This is not a general same-process sandbox. The source pin closes the named
importable-helper and import-cache predecessors; it is not a claim of integrity
against code that can replace the Python interpreter, modify the checked-in
package files before import, replace exported functions after import, deliberately
reload a modified public module, or subvert the operating-system process
boundary. Such code can fabricate local objects. Such an object still cannot
carry a valid signature from the externally held private key. Consumers making
consequential decisions must pin and verify the signer identity independently.

## Test and workflow fence

`tests/test_reinvestment_allocator.py` and its test-only support/driver modules
build a temporary package containing the production public API helpers,
executable-only worker helpers, pinned allocator resource, and provider stubs.
They cover valid external signatures, forged signatures, scope changes,
stale/future authority, post-import trust-root changes, post-import module and
POSIX primitive rebinding, provider-module poisoning, core-resource tampering,
receipt tampering, caller planning inputs, and legacy-core import blocking.

`tests/test_reinvestment_allocator_preimport.py` attacks ordinary pre-import API
and transport helper mutation, including the combined case, and verifies the
exact helper blob identities used by the public bootstrap.
`tests/test_reinvestment_allocator_preimport_seal.py` separately pre-seeds fake
API and transport objects in `sys.modules` and proves those import-cache objects
are not consumed as authority.

`.github/workflows/reinvestment-authority.yml` executes the complete
`test_reinvestment_allocator*.py` fence on Python 3.9 and 3.13 in both normal and
`python -O` modes. A queued or absent workflow is missing evidence, not a green
result.
