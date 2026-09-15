# Submission transport failover router

`concierge.submission_transport_router` is the send-free decision layer between a finished `bounty-submission-packet/v1` inner packet and any external GitHub, email, web-form, or other sponsor submission action.

It exists for a recurring last-mile failure: a finished paid-work packet is ready, the preferred provider route is attempted, and that provider returns a terminal failure such as a GitHub App `403 Resource not accessible by integration`. The dangerous manual response is to immediately try another route without proving whether the first attempt definitely failed, whether the sponsor actually authorizes the fallback, and whether the exact artifact revision is still the same. That can duplicate submissions, spam sponsors, and destroy a hot conversion.

The router performs **no external send**. It compiles provider evidence and a sponsor-evidence-bound route policy into exactly one public decision: use the primary route, use one exact fallback, stop because success is already confirmed, or hold.

## Position in the control plane

The boundaries are intentionally separate:

1. `submission_packet.py` proves that the selected paid-work artifact is `READY_FOR_HUMAN_SUBMISSION`.
2. **This router** proves whether transport history permits exactly one next route.
3. Commons' provider-linearizable outbound capability lease plus `outbound_capability_gate.py` still decide whether the current worker may perform a revenue-bearing provider mutation.
4. `submission_custody.py` records what was actually dispatched and what the sponsor did next.
5. settlement / payout rails remain separate.

A router receipt never substitutes for step 3 and never claims that step 4 occurred.

## Request contract

The JSON request contains exactly:

- `packet`: one inner packet produced by `bounty-submission-packet/v1`, including its `packet_sha256`. The router requires `READY_FOR_HUMAN_SUBMISSION`, an empty reason list, human-only authority, and re-verifies the canonical packet digest.
- `policy`: `sponsor-submission-transport-policy/v1`. It binds the canonical source, exact packet digest, an operator-verified sponsor-policy evidence digest, and an ordered list of opaque route IDs/classes. Every route has its own evidence digest and an explicit `failover_on` failure-class allowlist.
- `attempts`: an ordered prefix of that route list. Every attempt binds the exact packet, head SHA, and artifact evidence digest and carries only a provider-evidence SHA-256, not raw response text, credentials, addresses, or tokens.

The policy hash is an integrity binding, **not sponsor authentication**. The receipt therefore fixes `sponsor_route_authenticity_inferred=false`; callers must obtain and verify sponsor authority independently before constructing the policy.

## Outcomes

`READY_PRIMARY` means no provider attempt is recorded and the first policy route is the only candidate.

`READY_FALLBACK` requires all of the following: the preceding route outcome is `FAILED_CONFIRMED`, its exact failure class appears in that route's sponsor-policy `failover_on`, and another policy route remains.

`ALREADY_SUBMITTED` follows `SUCCESS_CONFIRMED` and exposes no next route.

`HOLD_AMBIGUOUS_PROVIDER_OUTCOME` follows `AMBIGUOUS`. Timeouts, lost receipts, unknown provider state, and other indeterminate outcomes must be represented as ambiguous rather than guessed to be failures. A later route attempt after ambiguity is rejected.

`HOLD_FAILURE_NOT_AUTHORIZED_FOR_FAILOVER` follows a confirmed failure whose class is absent from the sponsor policy.

`HOLD_NO_AUTHORIZED_ROUTE_REMAINS` follows an allowlisted terminal failure on the final policy route.

The router rejects route skipping, duplicate route/attempt IDs, attempts after success or ambiguity, advancement after an unallowlisted failure, cross-artifact attempt evidence, changed packet/policy hashes, non-canonical GitHub issue URLs, unsupported schemas/outcomes, undeclared fields, duplicate JSON keys, and non-finite JSON constants. Failure-class tokens containing timeout, unknown, network/connection, rate-limit/retry, temporary/transient/unavailable, or no-receipt semantics are also rejected as non-terminal; represent those outcomes as `AMBIGUOUS`.

## Example: GitHub App 403 to sponsor email

A policy can order two opaque routes:

```json
{
  "route_id": "upstream-github",
  "route_class": "github-pr",
  "route_evidence_sha256": "<64 lowercase hex>",
  "failover_on": ["PROVIDER_403_INTEGRATION_FORBIDDEN"]
}
```

followed by:

```json
{
  "route_id": "sponsor-email",
  "route_class": "email",
  "route_evidence_sha256": "<64 lowercase hex>",
  "failover_on": []
}
```

If the GitHub attempt is evidenced as `FAILED_CONFIRMED` with exactly `PROVIDER_403_INTEGRATION_FORBIDDEN`, the result is `READY_FALLBACK` naming only `sponsor-email`. If the GitHub response is missing or uncertain, use `AMBIGUOUS`; the result is HOLD and email is not selected.

Route IDs are opaque. Do not put an email address, token, private URL, provider credential, or raw sponsor message into the policy or receipt.

## CLI

```bash
python -m concierge.submission_transport_router request.json
python -m concierge.submission_transport_router request.json --summary
```

The CLI returns zero for `READY_PRIMARY`, `READY_FALLBACK`, and `ALREADY_SUBMITTED`; HOLD dispositions return 2. Invalid/tampered requests fail through `argparse` with a non-zero exit.

`strict_json_loads()` is available to integrations that need duplicate-key rejection. `verify_transport_decision(request, receipt)` recompiles the exact request and compares the whole deterministic receipt, including `decision_sha256`.

## Authority ceiling

Every decision receipt fixes these values:

- `external_send_authorized=false`
- `global_outbound_lease_required=true`
- `sponsor_route_authenticity_inferred=false`
- `sponsor_acceptance_inferred=false`
- `payment_inferred=false`
- `cash_claim=false`

A `READY_*` receipt is therefore **not permission to send**. It only says transport history does not itself forbid naming that one next route. Provider permission, buyer/sponsor authority, no-contact rules, content approval, the global outbound lease, and any Muse/fleet arbitration remain independent gates.
