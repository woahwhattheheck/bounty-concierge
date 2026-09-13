# Provider-truth outbound dedupe

`concierge.outbound_dedupe` is a deterministic pre-send gate for revenue outreach.
It exists because coordination feeds can lag the mail provider: a lead can still
look unsent in Slack after the provider already accepted the exact offer.

The gate does **not** connect to Gmail, Outlook, Slack, or any other service. A
caller performs the provider search, normalizes the result into the schema below,
and passes only the minimal receipt metadata needed to make the duplicate decision.

## Safety contract

A dispatch is allowed only when every declared provider query is complete and no
provider has a matching `sent` observation for both:

1. the canonical recipient; and
2. the stable, versioned `offer_key`.

A matching sent receipt always wins over an incomplete secondary provider query and
returns `DNR`. An incomplete query with no matching sent receipt returns `HOLD`.
Only a complete search with no match returns `CLEAR`.

Raw correspondence is intentionally outside the contract. Observation objects that
contain `subject`, `body`, `body_html`, `body_text`, `headers`, `html`, `raw`, or
`snippet` are rejected. Provider message ids are accepted as evidence identity but
are never emitted in the receipt; the result carries a short SHA-256 evidence id
instead.

The module performs no send, provider mutation, customer contact, authentication,
or credential handling.

## Input

```json
{
  "recipient": "carrie.rampp@fandm.edu",
  "offer_key": "banner-saas-parity-v1",
  "provider_queries": [
    {
      "provider": "gmail",
      "complete": true,
      "observations": [
        {
          "state": "sent",
          "message_id": "1a099a8e5597f623",
          "recipient": "carrie.rampp@fandm.edu",
          "offer_key": "banner-saas-parity-v1",
          "observed_at": "2026-09-13T07:26:17Z"
        }
      ]
    }
  ]
}
```

`offer_key` is deliberately a lowercase stable identity. Do not reuse an offer key
for materially different outreach. If a later campaign is intentionally distinct,
give it a new versioned key rather than bypassing the dedupe receipt.

`complete` is an assertion about the caller's provider search. Pagination,
provider errors, or an interrupted search must set it to `false`; an empty but
incomplete result is not permission to send.

## CLI

```bash
python -m concierge.outbound_dedupe request.json
python -m concierge.outbound_dedupe request.json --json
cat request.json | python -m concierge.outbound_dedupe -
```

Exit status:

- `0`: `CLEAR` — provider evidence authorizes dispatch.
- `2`: `HOLD` — provider query evidence is incomplete.
- `3`: `DNR` — matching provider-side sent evidence already exists.

Malformed or privacy-unsafe input is rejected by the CLI parser and does not
produce a dispatch authorization.

## Example safe receipt

```json
{
  "dispatch": false,
  "disposition": "DNR",
  "offer_key": "banner-saas-parity-v1",
  "reason_codes": [
    "PROVIDER_SENT_MATCH"
  ],
  "recipient": "carrie.rampp@fandm.edu",
  "signals": {
    "incomplete_providers": [],
    "matching_sent_count": 1,
    "matching_sent_evidence": [
      {
        "evidence_id": "7e09f11cc7c077fe6707",
        "observed_at": "2026-09-13T07:26:17Z",
        "provider": "gmail"
      }
    ],
    "provider_count": 1,
    "provider_observation_count": 1
  }
}
```

The evidence id above is illustrative; actual values are derived from the provider
name and opaque provider message id.
