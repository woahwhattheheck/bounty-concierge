# Provider-truth outbound dedupe v2

`concierge.outbound_dedupe` is an offline, fail-closed pre-send gate for revenue
outreach. It exists because internal coordination can lag the actual mail or
messaging providers: a lead may still look untouched after one provider already
accepted the exact outreach.

The gate does **not** connect to Gmail, Outlook, Slack, forms, or any provider.
A trusted caller searches every configured send-capable provider, normalizes the
minimal result below, and invokes this module with the independently configured
required-provider inventory.

## Authority model

A `CLEAR` result is permitted only when all of these are true:

1. the trusted `required_providers` inventory is non-empty;
2. there is exactly one query receipt for every required provider and no
   unexpected provider;
3. every query is bound to the same canonical recipient and versioned offer key;
4. every required query is complete and fresh under the trusted clock/policy;
5. every observation is at or before its query completion time; and
6. no provider reports a matching `sent` observation.

A known matching `sent` observation returns `DNR` even when another required
provider is missing, incomplete, or stale. Positive duplicate evidence is enough
to stop a send; incomplete negative evidence is never enough to authorize one.

Missing, incomplete, stale, malformed, target-mismatched, future-dated, or
unexpected-provider evidence fails closed. The trusted provider inventory is
passed out-of-band; an input document cannot declare its own authority universe.

`CLEAR` is only authority for the duplicate-suppression question. It does not
create an offer, approve its content, authorize a budget, prove customer intent,
perform a send, or establish revenue.

## Exact input

The request object accepts exactly:

```json
{
  "recipient": "buyer@example.com",
  "offer_key": "agent-rescue-v2",
  "provider_queries": [
    {
      "provider": "gmail",
      "recipient": "buyer@example.com",
      "offer_key": "agent-rescue-v2",
      "complete": true,
      "completed_at": "2026-09-13T10:29:00Z",
      "observations": [
        {
          "state": "sent",
          "message_id": "opaque-provider-id",
          "recipient": "buyer@example.com",
          "offer_key": "agent-rescue-v2",
          "observed_at": "2026-09-13T10:28:30Z"
        }
      ]
    }
  ]
}
```

Request, query, and observation objects use exact schemas. Arbitrary extra keys,
including correspondence-like fields, are rejected. JSON loaded through the CLI
also rejects duplicate keys at every depth.

Allowed observation states are `sent`, `draft`, and `received`. Only `sent`
blocks dispatch. Exact duplicate observations collapse; conflicting reuse of one
provider message ID fails closed.

Provider IDs are canonical lowercase identifiers. Offer keys are stable,
lowercase, versioned operational identities; materially different outreach
should use a new offer key rather than bypass this gate.

## Trusted provider inventory

The required provider set is deliberately **not** accepted from the JSON request.
It comes from trusted operating configuration at invocation time:

```bash
python -m concierge.outbound_dedupe request.json \
  --required-provider gmail \
  --required-provider outlook
```

If Gmail and Outlook are both send-capable for the operation, both must be in the
trusted inventory and both must have fresh complete query receipts before
`CLEAR` is possible. Supplying only a dummy provider, omitting Gmail, or adding
an unexpected provider cannot manufacture dispatch authority.

The default freshness window is 300 seconds and may be set from 30 to 3600
seconds with `--max-query-age-seconds`.

## Receipt and privacy boundary

The safe receipt never emits the raw recipient address or raw provider message
IDs. Instead it contains:

- a short SHA-256-derived `recipient_id`;
- SHA-256-derived evidence IDs for matching sent observations;
- the exact required/queried/missing/incomplete/stale provider sets;
- `provider_evidence_sha256`, which binds the normalized safe provider evidence,
  including full message-ID hashes;
- `receipt_sha256`, which binds the complete receipt core; and
- explicit authority flags showing that no send/provider mutation occurred.

The integrity digests prove internal byte-level consistency of the normalized
receipt. They are **not** external provider authenticity and are explicitly
labeled that way.

## Decision precedence

- `DNR` / exit `3`: one or more matching provider-side sent observations exist.
- `HOLD` / exit `2`: no known sent match exists, but required evidence is
  missing, incomplete, or stale.
- `CLEAR` / exit `0`: the exact trusted provider inventory is present,
  complete, fresh, target-bound, and contains no sent match.

Malformed input is rejected and does not produce a dispatch authorization.

## Verification

Focused authority regression:

```bash
python -m py_compile concierge/outbound_dedupe.py tests/test_outbound_dedupe.py
python -m unittest -v tests.test_outbound_dedupe
python -O -m unittest -v tests.test_outbound_dedupe
PYTHONHASHSEED=31337 python -m unittest -v tests.test_outbound_dedupe
```

The test matrix covers required-provider omission, dummy-provider substitution,
missing/incomplete/stale evidence, DNR-over-HOLD precedence, target mismatch,
future timestamps, observation-after-query inversion, exact-schema privacy
failures, duplicate/conflicting provider IDs, JSON duplicate keys, deterministic
evidence ordering/digests, and receipt privacy.
