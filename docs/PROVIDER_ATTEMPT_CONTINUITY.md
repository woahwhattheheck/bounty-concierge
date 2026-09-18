# Provider-attempt continuity

`provider_attempt_continuity` closes the evidence gap between a
`payout-delivery` decision and the next durable prior-mutation history.

The payout-delivery gate answers one question: **may this selected worker make
one exact provider mutation now?** A connector call still needs a second piece
of evidence afterward: what actually happened. A failed API call is not proof
that the provider saw nothing, and it is not a provider send receipt either.
Without an explicit bridge, workers can either resend an ambiguous attempt or
incorrectly mark failed work as dispatched.

## Input contract

The compiler binds two retained JSON objects:

1. the exact `bounty-concierge-payout-delivery-receipt/v1` that authorized the
   attempt; and
2. one `bounty-provider-attempt-report/v1` containing the attempt time, outcome,
   evidence class and SHA-256 of retained evidence.

Accepted outcome/evidence pairs are intentionally narrow:

| Outcome | Required evidence class | Meaning |
| --- | --- | --- |
| `CONFIRMED_SENT` | `PROVIDER_SENT_RECEIPT` | Provider returned a concrete send/publication receipt. |
| `CONFIRMED_NOT_SENT` | `POST_ATTEMPT_PROVIDER_CENSUS` | A fresh provider census proves the attempted message is absent. |
| `CONFIRMED_NOT_SENT` | `PRE_PROVIDER_BLOCK` | Execution was stopped before reaching the provider. |
| `AMBIGUOUS` | `CONNECTOR_ERROR` | The connector failed and provider mutation truth is unknown. |

A connector exception can therefore never be labeled `CONFIRMED_NOT_SENT` on
its own.

## Output contract

The receipt emits an exact `history_mutation` row compatible with the
`payout-delivery` history schema. Feed that row into the next complete
route-scoped history before asking for another gate.

The continuity state is one of:

- `RECORD_DISPATCH`: the attempt has explicit provider-send evidence;
- `RECAPTURE_AND_REAUTHORIZE_BEFORE_RETRY`: explicit evidence says no send
  occurred, but a retry still requires a new history capture and gate; or
- `HOLD_FOR_PROVIDER_CENSUS`: outcome is ambiguous and a retry must remain
  blocked until provider state is reconciled.

`custody_dispatch_allowed` is true only for `CONFIRMED_SENT`. This module
does not mutate collection custody itself, send messages, authenticate provider
receipts, move funds, or recognize cash/revenue.

## CLI

```sh
python -m concierge.provider_attempt_continuity compile \
  delivery.json attempt.json --output continuity.json

python -m concierge.provider_attempt_continuity verify \
  delivery.json attempt.json continuity.json --output verified.json
```

Outputs are create-exclusive; existing files and symlink targets are not
silently overwritten.

## Integration invariant

The generated `history_mutation` has the exact field set consumed by
`_payout_delivery_gate_core`. Focused regression coverage proves that an
`AMBIGUOUS` row makes a later payout-delivery gate return
`DUPLICATE_SEND_BLOCKED`, while a `CONFIRMED_NOT_SENT` row remains valid
history but this bridge still requires fresh recapture and authorization before
the operator retries.
