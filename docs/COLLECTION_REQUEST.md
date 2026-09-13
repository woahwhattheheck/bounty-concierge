# Collection Request Packets

`concierge.collection_request` turns bounded evidence for completed paid work into a deterministic, send-ready sponsor collection **draft**. It exists to make compensation intent explicit without overstating what the evidence proves.

## Authority model

A merged PR is useful completion evidence, but merge alone does not prove sponsor acceptance, an award, a payable obligation, earned revenue, or payment due. Therefore:

- `acceptance.kind = NONE` produces `READY_TO_REQUEST_ASSESSMENT`. The draft directly asks the sponsor to assess the merged contribution under the advertised terms and, if accepted, arrange the payout.
- `SPONSOR_ACCEPTED` or `AWARDED` requires a separate HTTPS evidence reference plus a SHA-256 commitment and produces `READY_TO_REQUEST_PAYMENT`.
- The packet always records `external_send=false`, `provider_mutation=false`, `payment_mutation=false`, `advertised_reward_is_debt=false`, and `advertised_reward_is_earned_revenue=false`.

The module does **not** send email, post GitHub comments, mutate wallets/providers, invoice anyone, or recognize revenue. A human or separately authorized sender owns transmission.

## Example: merged bug-bounty contribution

```json
{
  "schema": "bounty-collection-request-input/v1",
  "sponsor_name": "Acme Security",
  "work": {
    "repo": "acme/widget",
    "pr": 42,
    "canonical_url": "https://github.com/acme/widget/pull/42",
    "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "state": "MERGED",
    "advertised_amount": "90",
    "currency": "USD"
  },
  "payout_route": {
    "type": "PAYMENT_LINK",
    "value": "https://pay.example/abc"
  },
  "acceptance": {
    "kind": "NONE",
    "evidence_ref": null,
    "evidence_sha256": null
  }
}
```

Compile:

```bash
python -m concierge.collection_request request.json > packet.json
```

Verify exact derivation later:

```bash
python -m concierge.collection_request request.json --verify packet.json
```

## Collection-writing rule

The generated copy asks for the advertised compensation directly. It intentionally avoids soft eligibility language such as “am I eligible?” while still preserving evidence truth: merge-only work asks for sponsor **assessment**; accepted/awarded work asks for **payment**. This keeps the commercial intent strong and the evidentiary claim bounded.
