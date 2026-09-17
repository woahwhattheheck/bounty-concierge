# Reward settlement source-attestation certifier

`concierge.reward_settlement_certifier` is the trust-boundary companion to the merged-work reward settlement ledger. The ledger reconciles declared evidence into explicit states. The certifier answers the stronger question: **which of those declared sources are independently bound to a separately authenticated source registry?**

A caller-written `authority: PROVIDER`, `WALLET`, `BANK`, or `SPONSOR` field is not proof. A source becomes certifiable only when its exact `source_id`, `source_ref`, `source_sha256`, `observed_at`, and `authority` tuple is present in a registry whose canonical body verifies under the operator-held HMAC trust key. The registry is therefore an out-of-band capability: produce it only from a separately validated connector or retained-evidence adapter. Do not put the trust key in repository files, settlement input, registry JSON, logs, fixtures, or Slack. If an untrusted caller can choose the key or sign registries, the trust boundary is gone.

## States

The certifier can emit `UNTRUSTED_WORK_SOURCE`, `MERGE_ONLY_CERTIFIED`, `ADVERTISED_CERTIFIED`, `SPONSOR_AWARD_CERTIFIED`, `PAYOUT_TICKET_CERTIFIED`, `PAYOUT_RAIL_CERTIFIED`, `TRANSFER_EVIDENCE_CERTIFIED`, `PAID_CERTIFIED`, or `CLOSED_WITHOUT_REWARD_CERTIFIED`. `PAID_CERTIFIED` requires a trusted repository work source plus a trusted final incoming transfer state of `CONFIRMED`. Repeated snapshots for one transfer ID count once. Transfer amount/currency drift, state regression, contradictory terminal states, cross-case transfer reuse, and paid-vs-no-reward closure contradictions fail closed.

The certifier validates the original settlement schema again before applying trust. It rejects duplicate JSON keys, floating/non-finite JSON numbers, impossible timestamps, future observations, unsupported source authorities, malformed money fields, duplicate event/work identifiers, and source-registry remints. Registry timestamps and source observations are bound into the signed body. Certificate verification deterministically recomputes the complete artifact.

## Operator flow

Use a controlled process that possesses `REWARD_SETTLEMENT_TRUST_KEY`. Feed it (1) the reward-settlement input and (2) a registry produced by the trusted evidence adapter. The CLI intentionally does **not** ship a registry signer.

```text
REWARD_SETTLEMENT_TRUST_KEY='<operator-held-secret>' \
python -m concierge.reward_settlement_certifier \
  --input settlement-input.json \
  --registry trusted-sources.json \
  --certificate certification.json \
  --markdown certification.md
```

Outputs are created exclusively and never overwrite existing paths or follow output symlinks. If Markdown publication fails after the certificate is created, the certificate is removed so the pair cannot be mistaken for a complete publication.

## Authority ceiling

Certification is read-only evidence classification. It does not contact a sponsor, request a payout, mutate a provider/wallet/bank, invoice or collect, convert noncash units to USD, or recognize accounting revenue. `PAID_CERTIFIED` means the configured trust root bound exact evidence for a confirmed incoming transfer; it does not grant authority to book that fact into an accounting system.
