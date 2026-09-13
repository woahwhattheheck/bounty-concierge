# Payout discrepancy review

`concierge.payout_dispute` closes the gap between **accepted bounty terms** and an **observed settlement** without turning repository state, advertisements, or caller-provided evidence references into payment authority.

Use it after paid work is merged, the sponsor has separately accepted/awarded a concrete amount, and an actual settlement has been observed.

It answers one narrow question:

> Does the observed settlement numerically match the separately evidenced accepted/awarded terms, in the same currency?

It does **not** send sponsor messages, authenticate a web page merely because a URL or digest was supplied, perform FX conversion, claim a legal debt, decide that advertised bounty terms are earned revenue, refund an overpayment, or mutate a wallet/payment provider.

## Why this exists

The collection-request compiler can ask a sponsor to assess merged work or pay a separately accepted contribution. That still leaves a real cash-cycle failure mode: a settlement can arrive for an amount different from the accepted terms.

Human comparison is error-prone when several facts differ:

- the public advertisement can differ from the later accepted/awarded amount;
- a settlement can be zero, partial, exact, or greater than the accepted amount;
- the settlement can arrive in a different currency/token;
- acceptance evidence and settlement evidence are distinct authorities;
- a merge proves source state, not sponsor acceptance;
- a URL/digest binds the caller's evidence claim but does not authenticate the source by itself.

This compiler makes those distinctions explicit and deterministic.

## Input contract

The input schema is `bounty-payout-dispute-input/v1`.

```json
{
  "schema": "bounty-payout-dispute-input/v1",
  "sponsor_name": "Example Sponsor",
  "work": {
    "repo": "example/project",
    "pr": 42,
    "canonical_url": "https://github.com/example/project/pull/42",
    "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "state": "MERGED",
    "advertised_amount": "100",
    "currency": "USD"
  },
  "acceptance": {
    "kind": "AWARDED",
    "accepted_amount": "90",
    "currency": "USD",
    "evidence_ref": "https://example.com/award/42",
    "evidence_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "settlement": {
    "amount_received": "70",
    "currency": "USD",
    "transaction_id": "txn-123",
    "settled_at": "2026-09-13T14:00:00Z",
    "evidence_ref": "https://example.com/settlement/txn-123",
    "evidence_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  },
  "payout_route": {
    "type": "PAYMENT_LINK",
    "value": "https://pay.example.com/bryce"
  }
}
```

`acceptance.kind` is intentionally restricted to `SPONSOR_ACCEPTED` or `AWARDED`. There is no `NONE` path: merge alone is insufficient authority for a payout-discrepancy claim.

The `work.advertised_amount` remains in the receipt for provenance, but arithmetic is against `acceptance.accepted_amount`. If a sponsor accepted 90 USD on an advertised 100 USD bounty and 90 USD settled, the disposition is `NO_SHORTFALL`, not a fabricated 10 USD debt claim.

## Dispositions

### `READY_FOR_OWNER_UNDERPAYMENT_REVIEW`

The accepted/awarded and settlement currencies match, and the observed settlement is lower.

The packet contains an owner-review reconciliation draft. The draft asks the sponsor to reconcile the difference and explicitly allows fee/pending/action explanations. It does not assert legal debt or send itself.

### `NO_SHORTFALL`

Same currency, exact numerical match between accepted/awarded amount and observed settlement.

No sponsor draft is produced.

### `OVERPAYMENT_REQUIRES_OWNER_REVIEW`

Same currency, observed settlement is greater than accepted/awarded terms.

No retention, refund, return, wallet, or provider mutation authority is inferred. No sponsor draft is produced.

### `HOLD_CURRENCY_MISMATCH`

Accepted/awarded and settlement currencies differ.

No difference amount is computed. The tool never performs implicit FX conversion and never treats unlike units as comparable money.

## Evidence and authority

`evidence_ref` plus `evidence_sha256` is a deterministic binding supplied by the operator. This module does **not** fetch that evidence and does not prove:

- that a sponsor controls the URL;
- that the referenced content is authentic;
- that the evidence has legal effect;
- that a payment is owed;
- that a transaction belongs to the sponsor unless the operator established that separately.

The packet records this ceiling explicitly:

- `external_send = false`
- `provider_mutation = false`
- `payment_mutation = false`
- `wallet_mutation = false`
- `refund_or_return_authority = false`
- `legal_debt_claim = false`
- `advertised_reward_is_debt = false`
- `advertised_reward_is_earned_revenue = false`
- `merge_proves_acceptance = false`
- `evidence_ref_or_digest_authenticates_source = false`
- `fx_conversion_permitted = false`

For every non-`NO_SHORTFALL` packet, `owner_review_required_before_contact = true`; the tool never authorizes contact by itself.

## Determinism and hostile input handling

The compiler:

- uses `Decimal`, never binary floating-point, for amount comparison;
- canonicalizes finite bounded decimal strings;
- rejects negative settlement amounts and non-positive advertised/accepted amounts;
- accepts a zero observed settlement so a complete non-payment can be represented;
- binds `owner/repo`, PR number, canonical GitHub PR URL, and lowercase 40-hex submitted head;
- requires exact object keys and rejects unknown authority-expanding fields;
- rejects duplicate JSON keys and non-finite JSON constants;
- requires credential-free HTTPS evidence/payment-link URLs without fragments or embedded whitespace;
- requires canonical whole-second UTC settlement timestamps;
- produces a SHA-256 receipt over the complete packet core;
- verifies by exact recompilation, including rendered text and the authority ceiling.

## CLI

Compile:

```bash
python -m concierge.payout_dispute input.json > packet.json
```

Verify an existing packet:

```bash
python -m concierge.payout_dispute input.json --verify packet.json
```

Verification exits `0` only for exact deterministic derivation and prints:

```json
{"verified": true}
```

A modified difference, subject/body, source field, or authority bit fails verification.

## Tests

Focused hostile gate:

```bash
python -m unittest -v tests.test_payout_dispute
python -O -m unittest -v tests.test_payout_dispute
python -m py_compile concierge/payout_dispute.py tests/test_payout_dispute.py
```

The suite covers partial/full/zero/overpayment settlement, advertisement-vs-acceptance separation, currency mismatch, strict identity binding, URL/digest constraints, decimal exactness, duplicate/non-finite JSON rejection, control-character injection, timestamp canonicalization, unknown-field rejection, and receipt/authority tampering.
