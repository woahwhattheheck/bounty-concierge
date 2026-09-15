# Submission transport failover router

`concierge.submission_transport_router` is the send-free decision layer between a finished `bounty-submission-packet/v1` inner packet and any external GitHub, email, web-form, or other sponsor submission action.

It exists for a recurring last-mile failure: a finished paid-work packet is ready, the preferred provider route is attempted, and that provider appears to return a terminal failure such as a GitHub App `403 Resource not accessible by integration`. A second route is dangerous unless the system can prove all three things: the previous operation is the exact operation for this artifact and policy route, the provider outcome is authentic rather than caller narration, and the sponsor policy explicitly permits failover for that terminal failure class.

The router performs **no external send** and **does not sign provider receipts**. It compiles provider-authority receipts and a sponsor-evidence-bound route policy into exactly one public decision: use the primary route, use one exact fallback, stop because success is already confirmed, or hold.

## Trust boundary

Request JSON is untrusted orchestration input. In particular, a caller cannot unlock fallback by supplying a string such as `FAILED_CONFIRMED`, a plausible failure class, or a hash-shaped evidence pointer.

Every recorded provider attempt is accepted as outcome truth only when a verifier supplied **outside the request** authenticates a `provider-attempt-authority/v1` receipt. The reference verifier uses HMAC-SHA-256 with host/provider-adapter keys that are separately provisioned and never carried inside the routing request. Integrations may supply another verifier, but it occupies the same trusted boundary and must validate a provider/host authority rather than operator narration.

If an attempt exists and no verifier is supplied, the decision is `HOLD_PROVIDER_AUTHORITY_UNVERIFIED`. A failed HMAC has the same result. The public decision deliberately omits the unverified receipt's claimed `outcome`, `failure_class`, and provider-evidence digest so the router cannot launder unauthenticated claims into an authoritative-looking receipt.

The HMAC reference boundary is only as strong as key custody. The signing key must belong to the provider adapter or another independently trusted host component that observes the real provider result. Do **not** give that key to the orchestration component that constructs requests, do not place it in route policy, and do not commit it to the repository. The router exposes verification, not signing.

## Position in the control plane

The boundaries are intentionally separate:

1. `submission_packet.py` proves that the selected paid-work artifact is `READY_FOR_HUMAN_SUBMISSION`.
2. `compile_transport_operation()` derives the exact operation digest for one route before a provider adapter acts.
3. The separately trusted provider adapter performs the provider operation and emits an authenticated `provider-attempt-authority/v1` receipt binding the operation and result.
4. **This router** verifies that authority receipt and proves whether transport history permits exactly one next route.
5. Commons' provider-linearizable outbound capability lease plus `outbound_capability_gate.py` still decide whether the current worker may perform a revenue-bearing provider mutation.
6. `submission_custody.py` records what was actually dispatched and what the sponsor did next.
7. settlement / payout rails remain separate.

A router receipt never substitutes for the global outbound lease and never claims sponsor acceptance or payment.

## Request contract

The JSON request contains exactly:

- `packet`: one inner packet produced by `bounty-submission-packet/v1`, including its `packet_sha256`. The router requires `READY_FOR_HUMAN_SUBMISSION`, an empty reason list, human-only/advertised-only authority, a canonical GitHub issue source, and re-verifies the canonical packet digest.
- `policy`: `sponsor-submission-transport-policy/v1`. It binds the canonical source, exact packet digest, an operator-verified sponsor-policy evidence digest, and an ordered list of opaque routes.
- `attempts`: an ordered prefix of authenticated provider-attempt receipts. Every receipt binds the exact packet, head SHA, artifact-evidence SHA, policy SHA, route, authority, and deterministic operation SHA.

Each route has:

```json
{
  "route_id": "upstream-github",
  "route_class": "github-pr",
  "authority_id": "github-app-adapter-v1",
  "route_evidence_sha256": "<64 lowercase hex>",
  "failover_on": ["PROVIDER_403_INTEGRATION_FORBIDDEN"]
}
```

`authority_id` names the independently provisioned authority that may attest outcomes for that route. A receipt from a different authority is rejected even if its signature is otherwise valid under some other trusted key.

The policy hash is an integrity binding, **not sponsor authentication**. The decision receipt therefore fixes `sponsor_route_authenticity_inferred=false`; callers must obtain and verify sponsor route authority independently before constructing the policy.

## Exact operation binding

Before a provider adapter performs route `i`, call:

```python
operation = compile_transport_operation(packet, policy, i)
```

The operation digest covers:

- canonical source URL;
- packet SHA-256;
- artifact head SHA;
- artifact evidence SHA-256;
- policy SHA-256;
- route index;
- route ID and class;
- route authority ID;
- route evidence SHA-256.

This makes a receipt non-replayable across another artifact revision, packet, policy, route, authority, or route position. A provider adapter must copy `operation_sha256` into its authority receipt after observing the actual provider result.

## Provider-attempt authority receipt

A receipt has exactly these fields:

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
  "provider_evidence_sha256": "<digest of retained provider evidence>",
  "auth_tag_hmac_sha256": "<authority authentication tag>"
}
```

For the reference HMAC verifier, the provider adapter computes `auth_tag_hmac_sha256` over canonical JSON of all fields **except** `auth_tag_hmac_sha256`, using sorted keys, UTF-8, compact separators `(',', ':')`, `ensure_ascii=False`, and HMAC-SHA-256.

A signed receipt is evidence of what the trusted adapter attested. The adapter itself is responsible for turning raw provider semantics into `SUCCESS_CONFIRMED`, `FAILED_CONFIRMED`, or `AMBIGUOUS`. A timeout, missing response, lost receipt, connection/network failure, retry state, rate limit, temporary/transient condition, or otherwise unknown result must be `AMBIGUOUS`; the router also structurally rejects failure-class tokens carrying those semantics as terminal failure.

## Outcomes

`READY_PRIMARY` means no provider attempt is recorded and the first policy route is the only candidate. No provider-outcome verifier is required merely to name the first candidate.

`READY_FALLBACK` requires all of the following:

- the previous receipt is authenticated by the policy route's separately provisioned authority;
- the authenticated outcome is `FAILED_CONFIRMED`;
- its exact terminal failure class appears in that route's sponsor-policy `failover_on`;
- another policy route remains.

`ALREADY_SUBMITTED` follows an authenticated `SUCCESS_CONFIRMED` and exposes no next route.

`HOLD_PROVIDER_AUTHORITY_UNVERIFIED` follows a recorded attempt whose authority cannot be independently verified. This includes a valid-looking receipt when no verifier is supplied and a receipt carrying an invalid authentication tag.

`HOLD_AMBIGUOUS_PROVIDER_OUTCOME` follows an authenticated `AMBIGUOUS`. A later route attempt after ambiguity is rejected.

`HOLD_FAILURE_NOT_AUTHORIZED_FOR_FAILOVER` follows an authenticated terminal failure whose class is absent from the sponsor policy.

`HOLD_NO_AUTHORIZED_ROUTE_REMAINS` follows an authenticated allowlisted terminal failure on the final policy route.

The router rejects route skipping, duplicate route/attempt IDs, attempts after success or ambiguity, attempts after unverified provider authority, advancement after an unallowlisted failure, cross-artifact replay, changed policy/operation bindings, wrong route authority, non-canonical GitHub issue URLs, unsupported schemas/outcomes, undeclared fields, duplicate JSON keys, and non-finite JSON constants.

## Reference HMAC verifier

Library integrations should provision verifier keys outside request data:

```python
from concierge.submission_transport_router import (
    compile_transport_decision,
    make_hmac_authority_verifier,
)

verifier = make_hmac_authority_verifier({
    "github-app-adapter-v1": trusted_adapter_key_bytes,
    "smtp-adapter-v1": trusted_smtp_adapter_key_bytes,
})
receipt = compile_transport_decision(request, authority_verifier=verifier)
```

Keys must be 32..128 raw bytes. The returned verifier authenticates but never signs receipts.

The CLI can consume a separately provisioned keyring file:

```bash
python -m concierge.submission_transport_router request.json \
  --authority-keyring /run/secrets/submission-transport-verifier-keys.json
```

The keyring is a strict JSON object mapping `authority_id` to lowercase hex key bytes. It is a host trust input, not part of the submission request. If the keyring is omitted and attempts exist, the result is HOLD.

## Example: authenticated GitHub App 403 to sponsor email

Suppose the first policy route is `upstream-github` with authority `github-app-adapter-v1`, and its allowlist contains `PROVIDER_403_INTEGRATION_FORBIDDEN`. The next route is `sponsor-email` with a separate SMTP authority.

Only an authenticated provider receipt bound to the exact first-route operation and attesting:

```text
outcome=FAILED_CONFIRMED
failure_class=PROVIDER_403_INTEGRATION_FORBIDDEN
```

can produce `READY_FALLBACK` naming `sponsor-email`. A caller-written string with the same values, an arbitrary provider-evidence digest, a forged tag, an unknown authority, a receipt from a different packet/revision/route, or a missing verifier all HOLD or fail closed.

If GitHub's result is uncertain, the trusted adapter must attest `AMBIGUOUS`; email is not selected.

Route IDs are opaque. Do not put an email address, token, private URL, provider credential, raw provider response, or raw sponsor message into the policy or decision receipt.

## CLI and verification

```bash
python -m concierge.submission_transport_router request.json
python -m concierge.submission_transport_router request.json --summary
```

The CLI returns zero for `READY_PRIMARY`, `READY_FALLBACK`, and `ALREADY_SUBMITTED`; HOLD dispositions return 2. Invalid/tampered requests fail through `argparse` with a non-zero exit.

`strict_json_loads()` rejects duplicate keys and non-finite constants. `verify_transport_decision(request, receipt, authority_verifier=...)` recompiles the exact request against the same trust boundary and compares the whole deterministic decision, including `decision_sha256`.

## Authority ceiling

Every decision receipt fixes these values:

- `external_send_authorized=false`
- `global_outbound_lease_required=true`
- `sponsor_route_authenticity_inferred=false`
- `sponsor_acceptance_inferred=false`
- `payment_inferred=false`
- `cash_claim=false`

It also records whether provider-outcome authority was required and whether all recorded outcomes were verified.

A `READY_*` decision is therefore **not permission to send**. It only says verified transport history does not itself forbid naming that one next route. Provider permission, buyer/sponsor authority, no-contact rules, content approval, the global outbound lease, and Muse/fleet arbitration remain independent gates.
