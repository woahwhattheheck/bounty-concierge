# Sponsor Adjudication → Collection Bridge

`concierge.adjudication_collection` closes the commercial-control gap between **host-authorized sponsor adjudication custody** and an owner-reviewed payment-request draft.

Sponsor adjudication now separates two kinds of truth:

1. a credential-owning host authorizes the exact sponsor-event scope; and
2. the retained adjudication report derives claim-unit state from those events without turning rewards or payment reports into cash.

The general `collection_request` module is intentionally phrased around an *advertised* bounty amount. A later sponsor `REWARD_OFFERED` amount can be partial, adjusted, or negotiated, so this bridge does **not** relabel an offered amount as advertised terms.

Instead, it accepts a retained adjudication report, runs the public authority-aware `verify_report()` path, and requires #162's host-HMAC report-generation binding before using any derived claim-unit field.

## Why the report-generation binding matters

`report_sha256` is an integrity digest, not host authority. Current sponsor adjudication (#162) adds a second host HMAC over the complete compiled report projection and chains it to the original signed manifest generation. The bridge deliberately relies on the public authority-aware `verify_report()` boundary, so editing a derived claim-unit reward, membership, status, action, or other retained output and merely recomputing the public digest cannot create a payment draft.

This keeps sponsor-adjudication semantics in one implementation rather than duplicating its state machine downstream. The bridge only adds commercial-generation rules after the upstream authority boundary is green.

## Input

```json
{
  "schema": "bounty-adjudication-collection-input/v1",
  "adjudication_report": {
    "schema_version": 1,
    "program": {
      "program_id": "program-1",
      "sponsor": "Acme Security",
      "source_ref": "https://example.invalid/program",
      "sponsor_authority": {"...": "retained host authority"}
    },
    "...": "retained adjudication report"
  },
  "claim_unit_id": "unit-1",
  "work": {
    "repo": "acme/widget",
    "pr": 42,
    "canonical_url": "https://github.com/acme/widget/pull/42",
    "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "state": "MERGED"
  },
  "payout_route": {
    "type": "PAYMENT_LINK",
    "value": "https://pay.example/bryce"
  }
}
```

The bridge requires the same host HMAC/provider/principal environment needed by sponsor-adjudication historical verification. It **refuses** `BOUNTY_SPONSOR_ADJUDICATION_TEST_ONLY_ALLOW_UNSIGNED=1`; an unsigned test bypass can never become a collection path.

`payout_route` is `null` unless the bridge can produce an owner-review reward draft. This avoids accumulating payout-route material while the sponsor is still adjudicating, after a decline/duplicate, after a sponsor reports payment, or while reward evidence lacks a retained HTTPS permalink.

## Deterministic dispositions

| Authenticated claim-unit state | Bridge disposition | Draft? |
|---|---|---|
| `reward_offered` + retained credential-free HTTPS reward evidence | `OWNER_REVIEW_REQUIRED` | Yes, explicitly non-send-ready |
| `reward_offered` without such a permalink | `HOLD_FOR_ACCEPTANCE_EVIDENCE_URL` | No |
| `paid_evidence_pending` | `HOLD_FOR_SETTLEMENT_EVIDENCE` | No |
| `submitted`, `sponsor_verified`, `adjudicating` | `WAIT_SPONSOR` | No |
| `declined`, `duplicate` | `DO_NOT_REQUEST` | No |

`PAYMENT_REPORTED` never creates another payment ask. It sends the workflow toward independent settlement evidence instead.

## One commercial generation, even if the route changes

For an owner-review reward generation the output contains `collection_key`, a SHA-256 commitment over the sponsor-authorized commercial identity only:

- program ID + sponsor identity;
- canonical sponsor claim-unit ID;
- host-bound finding/submission membership; and
- exact offered amount/currency.

The **payout route, caller-supplied merged-work record, and evidence permalink are intentionally excluded** from `collection_key`. They remain receipt-bound packet data, but correcting or reacquiring any of them cannot pretend the same sponsor reward is a second commercial collection generation. A corrected payment link or wallet, corrected caller work record, or reacquired reward permalink therefore changes the packet receipt as applicable, but does not pretend the same sponsor reward is a second commercial collection generation.

A later exact reward reaffirmation also does not mint a second key. If an original reward event has only a non-HTTPS internal reference, a later identical host-authorized reward event with a retained HTTPS permalink may unlock the first owner-review collection generation.

## Work provenance boundary

The sponsor reward and claim-unit identity are host-authorized; the `work` tuple is not. The bridge therefore never presents caller-supplied repo/PR/head as factual merged rewarded work and never emits a send-ready disposition. In the owner-review draft it is rendered only as a **caller-supplied unverified candidate**. An unrelated/fabricated syntactically valid GitHub tuple must still stop at `OWNER_REVIEW_REQUIRED`. A future carrier may add a separately trusted GitHub merge/readback receipt plus a mechanical relation from that work identity to the selected sponsor claim unit; until then, this module deliberately does not cross that authority boundary.

## Authority ceiling

The bridge is deterministic preparation only. It does not:

- send a message or contact a sponsor;
- mutate email, GitHub, Stripe, a wallet, a bank, or any provider;
- initiate a payout;
- authenticate a sponsor merely because retained bytes have a digest;
- turn a reward offer into debt, cash, payment-due proof, or recognized revenue; or
- prove the caller-supplied merged-work fields independently.

The packet retains sponsor-authority provider/principal/scope provenance plus the signed manifest/report-projection digests, but not either HMAC signature itself. Because the caller-supplied GitHub work tuple is not independently verified or mechanically bound to the sponsor claim unit, the strongest disposition is `OWNER_REVIEW_REQUIRED`, never send-ready. The draft labels the work URL/head as caller-supplied unverified candidates and explicitly requires independent verification of merge state and claim-unit relation before any send. The output also requires an owner or separately authorized sender.

## CLI

Compile:

```bash
python -m concierge.adjudication_collection input.json > packet.json
```

Verify exact derivation later:

```bash
python -m concierge.adjudication_collection input.json --verify packet.json
```

The loader rejects duplicate JSON keys, floats/non-finite numbers, oversized JSON integers, symlink-following where the platform supports `O_NOFOLLOW`, non-regular files, and files larger than 1 MiB before unbounded reads.


## Display-safety boundary

Text that can reach the owner-facing draft rejects Unicode control, format, surrogate, and line/paragraph-separator characters (including bidi overrides) in addition to ordinary C0 controls. This prevents visually forged owner-review/payment-route text from being smuggled through sponsor names, work fields, or payout-route values.
