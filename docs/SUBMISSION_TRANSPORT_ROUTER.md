# Submission transport failover router

`concierge.submission_transport_router` is a send-free decision layer for paid-work submission transport. Given a canonically verified `READY_FOR_HUMAN_SUBMISSION` packet, an ordered sponsor-route policy, and retained provider-attempt receipts, it answers one narrow question: may the system name exactly one next transport route without inventing provider state or risking a duplicate submission?

It never performs email, GitHub mutation, form submission, sponsor contact, payout request, or payment claim. A `READY_*` decision is not permission to send. Commons' provider-linearizable outbound lease, provider permissions, content approval, no-contact rules, and Muse/fleet arbitration remain separate required gates.

## Production surfaces share one trust boundary

Supported API:

```python
from concierge.submission_transport_router import (
    compile_transport_operation,
    compile_transport_decision,
    verify_transport_decision,
)
```

The implementation lives in `concierge._submission_transport_router_core`, but that module is no longer a weaker alternate entrypoint. Direct core import, direct core CLI execution, and the public wrapper all use the same production functions:

- canonical packet verification through `submission_custody._verify_submission_packet`;
- one fixed root-owned provider-receipt ledger;
- no caller verifier parameter;
- no caller keyring parameter;
- no ledger-path parameter or environment selector;
- no production CLI authority selector.

The public wrapper re-exports those exact safe functions rather than monkeypatching the core at import time. Import order therefore cannot choose a weaker packet or provider-authority contract.

## Canonical submission packet contract

Every production operation and decision delegates packet ingress to `submission_custody._verify_submission_packet`. Transport inherits the repository's single packet contract:

- exact packet, evidence, and authority shapes;
- canonical GitHub source and pull-request identity;
- source-repository / pull-request-repository consistency;
- strictly positive advertised reward;
- changed-path subset of allowed paths;
- nonempty PASS-only test evidence;
- nonempty PASS-only acceptance evidence;
- exact packet SHA-256 binding;
- secret-shaped field rejection.

There is no hand-written transport-specific packet validator to bypass by importing the core first.

## Fixed host-retained provider receipt ledger

Recorded provider outcomes are authoritative only when the exact parsed receipt is retained at:

```text
/var/lib/bounty-concierge/provider-attempt-authority/<operation_sha256>.json
```

The path is a literal in the production verifier. It is not selected by request JSON, CLI argument, environment variable, public function parameter, or public wrapper.

The reader opens the directory and receipt without following symlinks and fails closed when the platform cannot support the required `dir_fd` / `O_NOFOLLOW` controls. The directory must:

- be a directory owned by root (`uid 0`);
- not be group- or world-writable.

The receipt file must:

- be opened relative to the already-opened trusted directory;
- be a regular file owned by root;
- have exactly one hard link;
- not be group- or world-writable;
- not be world-readable;
- be 1..32768 bytes;
- contain UTF-8, duplicate-key-free, finite JSON;
- parse to the exact receipt object presented to the compiler.

Missing, insecure, malformed, unreadable, or mismatched evidence returns false. The decision becomes `HOLD_PROVIDER_AUTHORITY_UNVERIFIED`; no route is named and the unverified claimed outcome/failure/evidence are omitted from the public completed-attempt projection.

The host/provider adapter or supervisor owns ledger publication. Orchestration callers do not write the ledger and do not choose its location.

## Route policy

A `sponsor-submission-transport-policy/v1` binds the exact packet and ordered routes. Each route contains:

```json
{
  "route_id": "upstream-github",
  "route_class": "github-pr",
  "authority_id": "github-app-adapter-v1",
  "route_evidence_sha256": "<64 lowercase hex>",
  "failover_on": ["PROVIDER_403_INTEGRATION_FORBIDDEN"]
}
```

`authority_id` names the provider/host authority expected to retain the attempt receipt. Route and policy evidence digests are integrity bindings; they do not authenticate sponsor intent. Every decision keeps `sponsor_route_authenticity_inferred=false`.

## Exact operation binding

Before a trusted provider adapter acts on route index `i`:

```python
operation = compile_transport_operation(packet, policy, i)
```

The operation digest binds:

- canonical source URL;
- packet SHA-256;
- artifact head SHA;
- artifact evidence SHA-256;
- policy SHA-256;
- route index;
- route ID and class;
- authority ID;
- route-evidence SHA-256.

A receipt retained for one operation cannot authorize a different packet, revision, artifact, policy, route, authority, or route position.

## Provider-attempt receipt

Attempts use `provider-attempt-authority/v1`:

```json
{
  "schema": "provider-attempt-authority/v1",
  "authority_id": "github-app-adapter-v1",
  "operation_sha256": "<operation digest>",
  "attempt_id": "try-1",
  "route_id": "upstream-github",
  "packet_sha256": "<packet digest>",
  "head_sha": "<40 lowercase hex>",
  "artifact_evidence_sha256": "<artifact digest>",
  "policy_sha256": "<policy digest>",
  "outcome": "FAILED_CONFIRMED",
  "failure_class": "PROVIDER_403_INTEGRATION_FORBIDDEN",
  "provider_evidence_sha256": "<retained provider evidence digest>",
  "auth_tag_hmac_sha256": "<legacy envelope field>"
}
```

Production authority comes from exact root-owned ledger custody, not from a caller-selected HMAC keyring. The legacy HMAC-shaped field remains part of the v1 envelope for compatibility and is syntax-bound, but it is not the production trust root.

Timeout, unknown, network/connection, rate-limit/retry, temporary/transient/unavailable, and no-receipt semantics are not terminal failures. They must be represented as `AMBIGUOUS`.

## Decisions

- `READY_PRIMARY`: no provider attempt exists; names the first policy route.
- `READY_FALLBACK`: the exact previous receipt is retained in the fixed ledger, reports `FAILED_CONFIRMED`, its failure class is allowlisted for that route, and another ordered route remains.
- `ALREADY_SUBMITTED`: the exact retained receipt reports `SUCCESS_CONFIRMED`; no next route.
- `HOLD_PROVIDER_AUTHORITY_UNVERIFIED`: a recorded attempt lacks exact retained host authority.
- `HOLD_AMBIGUOUS_PROVIDER_OUTCOME`: retained provider state is ambiguous.
- `HOLD_FAILURE_NOT_AUTHORIZED_FOR_FAILOVER`: retained terminal failure is not allowlisted.
- `HOLD_NO_AUTHORIZED_ROUTE_REMAINS`: an allowlisted terminal failure occurred on the final route.

Attempts after unverified authority, success, ambiguity, or an unallowlisted failure are rejected.

## Test-only algorithm observation

`_compile_transport_test_observation` is a private isolated-test seam. It may inject a verifier to exercise route mechanics, but it cannot emit a production decision:

- schema is `submission-transport-test-observation/v1`;
- production disposition is always `HOLD_TEST_ONLY_AUTHORITY`;
- production authority is always false;
- `next_route` is always null;
- there is no production `decision_sha256`;
- external send authority is always false.

It can report a `candidate_disposition` and candidate route ID for hostile unit tests, but those fields are explicitly non-production observations. The production library and both CLIs never call this seam.

## CLI

Both commands use the same fixed production trust path:

```bash
python -m concierge.submission_transport_router request.json
python -m concierge._submission_transport_router_core request.json
```

`--summary` is the only optional flag. There is intentionally no verifier, keyring, or ledger selector. `READY_PRIMARY`, `READY_FALLBACK`, and `ALREADY_SUBMITTED` return zero; HOLD dispositions return 2.

## Decision authority ceiling

Every production decision states:

- `provider_authority_trust_root=fixed-root-owned-provider-receipt-ledger`;
- `canonical_packet_verifier=submission_custody._verify_submission_packet`;
- `caller_verifier_injection_supported=false`;
- `caller_keyring_selection_supported=false`;
- `caller_trust_root_selection_supported=false`;
- `direct_core_production_safe=true`;
- `external_send_authorized=false`;
- `global_outbound_lease_required=true`;
- `sponsor_route_authenticity_inferred=false`;
- `sponsor_acceptance_inferred=false`;
- `payment_inferred=false`;
- `cash_claim=false`.

Transport readiness is only one input to the broader outbound control plane.
