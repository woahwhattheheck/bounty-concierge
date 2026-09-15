# Submission transport failover router

`concierge.submission_transport_router` is a send-free decision layer for paid-work submission transport. It answers one narrow question: after a finished submission packet and an ordered sponsor route policy exist, does verified transport history permit naming exactly one next route?

It never performs email, GitHub mutation, form submission, sponsor contact, payout, or payment claim. A `READY_*` decision is not permission to send. Commons' provider-linearizable outbound lease, provider permissions, no-contact rules, content approval, and Muse/fleet arbitration remain separate required gates.

## Why this exists

A recurring conversion seam is:

1. a finished artifact is `READY_FOR_HUMAN_SUBMISSION`;
2. the preferred provider route is attempted;
3. the provider appears to reject the operation, for example a GitHub App `403`;
4. a sponsor-documented fallback exists, often email.

Blindly trying the fallback is unsafe. A timeout, lost response, successful operation with a lost receipt, or caller-authored `"FAILED_CONFIRMED"` can create duplicate submissions and spam a hot lead.

The router therefore requires both route-policy evidence and independently retained provider-attempt evidence before fallback is possible.

## Public vs private surface

Supported production API:

```python
from concierge.submission_transport_router import (
    compile_transport_operation,
    compile_transport_decision,
    verify_transport_decision,
)
```

The deterministic routing engine lives in private module
`concierge._submission_transport_router_core`. That private engine accepts verifier dependency injection for isolated hostile tests. **It is not a production trust boundary and is not the supported import surface.**

The public module deliberately does **not** expose:

- an `authority_verifier=` parameter;
- a receipt-ledger path parameter;
- an environment variable selecting a verifier or ledger;
- `make_hmac_authority_verifier`;
- a CLI trust-root selector.

Ordinary request/library callers therefore cannot promote `lambda _: True`, an attacker-selected keyring, or an attacker-selected receipt directory through the supported compiler.

## Host-retained provider receipt ledger

The public compiler authenticates recorded provider outcomes only by exact receipt equality against one fixed host ledger:

```text
/var/lib/bounty-concierge/provider-attempt-authority/<operation_sha256>.json
```

The directory path is a literal inside the public verifier. It is not selected by request, CLI, environment, or public function argument.

The ledger directory must:

- exist as a directory;
- be owned by root (`uid 0`);
- not be group- or world-writable.

Each receipt file must:

- be opened without following a final symlink when the platform supports `O_NOFOLLOW`;
- be a regular file;
- be owned by root;
- not be group- or world-writable;
- not be world-readable;
- be 1..32768 bytes;
- contain strict duplicate-key-free UTF-8 JSON.

The filename is the exact deterministic `operation_sha256`. The retained JSON object must equal the presented provider receipt byte-for-byte after strict JSON parsing. Missing, unreadable, insecure, malformed, or mismatched host evidence returns false; the private engine then produces `HOLD_PROVIDER_AUTHORITY_UNVERIFIED`.

The provider adapter or host supervisor owns ledger publication. Orchestration callers do not write this directory and do not choose its path.

The public receipt truth-labels:

- `provider_authority_trust_root = fixed-root-owned-provider-receipt-ledger`;
- `caller_verifier_injection_supported = false`;
- `caller_trust_root_selection_supported = false`.

## Canonical submission packet contract

The public adapter reuses `submission_custody._verify_submission_packet` directly. Transport therefore inherits the repository's canonical packet contract instead of maintaining a weaker duplicate:

- exact packet/evidence/authority shapes;
- canonical source and PR identity;
- source-repo / PR-repo consistency;
- positive advertised reward;
- changed-path allowlist enforcement;
- PASS-only test and acceptance evidence;
- exact packet SHA-256 binding.

The private engine's older packet checker is overridden by the public adapter at import time and is not the supported entrypoint.

## Route policy

A `sponsor-submission-transport-policy/v1` binds the exact packet and ordered routes. Each route includes:

```json
{
  "route_id": "upstream-github",
  "route_class": "github-pr",
  "authority_id": "github-app-adapter-v1",
  "route_evidence_sha256": "<64 lowercase hex>",
  "failover_on": ["PROVIDER_403_INTEGRATION_FORBIDDEN"]
}
```

`authority_id` names the provider/host authority that produced the retained receipt. `route_evidence_sha256` and `policy_evidence_sha256` are integrity bindings, not sponsor authentication; the decision keeps `sponsor_route_authenticity_inferred=false`.

## Exact operation binding

Before a trusted provider adapter acts on route index `i`:

```python
operation = compile_transport_operation(packet, policy, i)
```

The operation SHA binds:

- canonical source URL;
- packet SHA-256;
- artifact head SHA;
- artifact evidence SHA-256;
- policy SHA-256;
- route index;
- route ID/class;
- authority ID;
- route evidence SHA-256.

A receipt retained under one operation cannot authorize another packet, revision, policy, route, authority, or route position.

## Provider-attempt receipt

Recorded attempts use the private engine's `provider-attempt-authority/v1` envelope:

```json
{
  "schema": "provider-attempt-authority/v1",
  "authority_id": "github-app-adapter-v1",
  "operation_sha256": "<operation digest>",
  "attempt_id": "try-1",
  "route_id": "upstream-github",
  "packet_sha256": "<packet digest>",
  "head_sha": "<40 lowercase hex>",
  "artifact_evidence_sha256": "<artifact evidence digest>",
  "policy_sha256": "<policy digest>",
  "outcome": "FAILED_CONFIRMED",
  "failure_class": "PROVIDER_403_INTEGRATION_FORBIDDEN",
  "provider_evidence_sha256": "<retained provider evidence digest>",
  "auth_tag_hmac_sha256": "<private-engine envelope field>"
}
```

The public trust decision is **not** caller HMAC selection. It is exact equality with the fixed root-owned host receipt ledger. The HMAC field remains part of the private engine's deterministic envelope and test surface; production authority comes from host-retained exact receipt custody.

Timeout, unknown, network/connection, rate-limit/retry, temporary/transient/unavailable, and no-receipt semantics are not terminal failure classes. They must be represented as `AMBIGUOUS`.

## Decisions

- `READY_PRIMARY`: no provider attempt exists; names the first policy route.
- `READY_FALLBACK`: the exact previous receipt is retained in the fixed host ledger, its outcome is `FAILED_CONFIRMED`, its exact terminal failure class is sponsor-policy allowlisted, and another route remains.
- `ALREADY_SUBMITTED`: retained provider receipt says `SUCCESS_CONFIRMED`; no next route.
- `HOLD_PROVIDER_AUTHORITY_UNVERIFIED`: a recorded attempt lacks exact retained host authority.
- `HOLD_AMBIGUOUS_PROVIDER_OUTCOME`: retained provider result is ambiguous.
- `HOLD_FAILURE_NOT_AUTHORIZED_FOR_FAILOVER`: retained terminal failure is not allowlisted.
- `HOLD_NO_AUTHORIZED_ROUTE_REMAINS`: retained allowlisted terminal failure occurred on the final route.

Attempts after unverified authority, success, ambiguity, or unallowlisted failure are rejected.

## CLI

```bash
python -m concierge.submission_transport_router request.json
python -m concierge.submission_transport_router request.json --summary
```

There is intentionally no verifier/keyring/ledger selector. Host authority configuration is fixed outside caller control.

The CLI returns zero for `READY_PRIMARY`, `READY_FALLBACK`, and `ALREADY_SUBMITTED`; HOLD dispositions return 2.

## Authority ceiling

Every public decision keeps:

- `external_send_authorized=false`;
- `global_outbound_lease_required=true`;
- `sponsor_route_authenticity_inferred=false`;
- `sponsor_acceptance_inferred=false`;
- `payment_inferred=false`;
- `cash_claim=false`.

Transport readiness is only one input to the broader outbound control plane.
