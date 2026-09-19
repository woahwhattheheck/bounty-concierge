# Claim economic replay: transitive source generation

The installed `concierge claim` boundary now evaluates economics using one
independently loaded, source-pinned generation. It does **not** introduce a new
valuation engine, change a currency floor, expand a supported decision, or
change the live process-clock freshness limit.

## Why this is necessary

A captured public compiler function still resolves its own module globals.
Changing the public paid-work `_currency_floor` could therefore change a request
from `SKIP_ECONOMICS` to `GO`, and a matching forged receipt could pass both replay
and equality checks. Capturing just the outer verifier was insufficient.

## Retained implementation

The former `claim_economic_admission.py` is preserved byte-for-byte as
`_claim_economic_admission_impl.py`. The three existing paid-work/fleet source
files are unchanged. `_claim_economic_generation.py` verifies the four Git blob
identities, including their Git type/length framing, before executing any member
into a private module graph. Every `concierge` import within that graph resolves
only to an already retained member; unsupported imports fail closed. None of the
private modules is published through `sys.modules`.

Bounded regular-file descriptor reads reject missing, empty, oversized,
symlinked, changed, or wrong-generation source. An unavailable generation raises
`ImportError` during admission-module initialization; it never falls back to an
already imported public compiler. Source files must be included in the installed
package; bytecode-only or zip-only distributions are not supported by this gate.

The public admission module retains diagnostic/helper aliases for compatibility.
They belong to a separate generation from the production verifier. The private
verifier converts its typed errors to the existing public exception class,
preserving callers' error handling, proof schema, and receipt/policy semantics.
The existing entrypoint captures the new verifier through its existing factory.

## Trust boundary

This protects against rebinding public business-module globals, replacing nested
fleet/compiler/verifier references, and mutating exported helper defaults after
the production generation is initialized. Standard-library module attributes are
snapshotted at import to avoid later top-level attribute rebinding.

This is **not an in-process security sandbox**. Installation provenance,
interpreter, standard library, and initial loader code are trusted. Arbitrary
interpreter introspection, rewriting closure cells/code objects, altering the
standard library's transitive internals, or replacing the installed loader are
outside this contract. The Git blob pins identify the retained repository bytes;
they are not a signature or independent proof of who installed those bytes.

An intentional change to any retained source must update the corresponding pin
and run the complete claim, paid-work, and generation regression suites. A pin
must never be changed merely to make an unexplained mismatch disappear.

## Evidence and authority

The regression uses an advertised payout of 110, model/tool cost 10, and 1.01
engineering hours in the fixture's native currency. The true net amount is 100,
but its hourly return is below the fixture policy's 100 floor. A patched public
compiler can still construct its erroneous `GO`; the production verifier must
reject that receipt and must preserve refusal of the authentic `SKIP_ECONOMICS`.
Valid retained `GO` proofs must remain byte-identical under the public mutations.
The entrypoint regression requires zero provider-preflight/availability calls.

A successful result still authorizes only local claim-instruction admission. It
does not claim externally, contact a sponsor, submit work, move funds, prove
payment, or recognize revenue.

Source credit: Z-Sentinel and Z-SOL-17-B. Diagnosis/review credit: Z-Kepler,
Z-Malachite-731F, and Devin-Local. Transitive recovery: Z-Kestrel-FinOps.

The existing Python 3.13 workflow job retains an exact-checkout source archive
for one day so connector-only workers can reproduce it. The artifact is source
transport, **not** an execution-success receipt. No additional workflow or job
is introduced; the existing normal/optimized test commands include the new
generation suite. The checkout is pinned to the event head.
