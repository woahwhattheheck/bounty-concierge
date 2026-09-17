# Accepted-work collection bridge

This additive recovery closes one semantic gap between the merged reward-settlement product and the certified collections queue: a GitHub merge is not the same fact as **explicit sponsor/provider acceptance of the delivered work**.

The canonical pre-donor settlement lineage preserved `WORK_ACCEPTED` as a distinct event concept. The landed donor and its downstream certifier/collection queue correctly preserve settlement truth, but collapse merge-only work into `MERGED_UNSETTLED`. This bridge restores the missing distinction without rewriting those products.

## Composition

`concierge.accepted_work_collection_bridge` composes:

1. the exact existing settlement document;
2. the signed trusted-source registry already used by settlement certification;
3. the existing certified settlement collection queue;
4. a separate acceptance manifest binding one `ACCEPTED` record to the exact `case_id`, `owner/repo`, PR number, and merge commit.

Acceptance sources must be independently present as an exact tuple in the signed trust registry and must use `SPONSOR` or `PROVIDER` authority. Repository merge/approval evidence is deliberately rejected as commercial acceptance.

Acceptance also requires a **dedicated retained source identity**. A source already used for the work merge, advertised bounty, sponsor award, eligibility decision, payout ticket/rail, transfer, or closure cannot be relabeled as acceptance. The signed registry already rejects fingerprint reminting under a new source ID, so this closes both same-ID reuse and same-evidence remint paths inside the composed trust model.

Acceptance evidence must be observed at or after the exact merge, no later than the acceptance-manifest generation, and within the explicit freshness window. The manifest itself may not postdate the signed registry generation that contains its acceptance source.

## Finish states

The bridge never upgrades settlement truth. It adds a separate owner-review projection:

- `PAID_CLOSED`
- `CLOSED_NO_REWARD`
- `MERGED_WITHOUT_ACCEPTANCE_EVIDENCE`
- `ACCEPTANCE_NEEDS_TRUST_EVIDENCE`
- `ACCEPTANCE_NEEDS_REFRESH`
- `ACCEPTED_UNPAID_COLLECTIONS_REVIEW`
- `ACCEPTED_UNPAID_SETTLEMENT_EVIDENCE_REVIEW`
- `ACCEPTED_UNPAID_HOLD`

An accepted record reaches `ACCEPTED_UNPAID_COLLECTIONS_REVIEW` only when the existing certified queue independently already classifies the case as an award/ticket/rail/pending-transfer follow-up candidate. Acceptance alone never invents an award, debt, payout obligation, transfer, or payment. If settlement still lacks certified award evidence, the result is `ACCEPTED_UNPAID_SETTLEMENT_EVIDENCE_REVIEW`.

## Truth boundaries

- merge != acceptance
- acceptance != sponsor award
- acceptance != payout obligation
- acceptance != transfer
- acceptance != payment
- payment evidence != accounting revenue recognition

Every output keeps outbound/send, payout-request, provider/wallet/bank mutation, payment-claim, and accounting-revenue authority hard false. Muse/fleet single-writer arbitration remains a separate gate before any external contact.

## Lineage

PR #218 remains a useful accidental donor. #222/#225/#226/#227/#228 remain the authoritative landed trust and collection chain. This recovery does not replace their source or attribution. It restores the canonical acceptance distinction on top of literal current main so downstream operations can distinguish “merged” from “explicitly accepted” without weakening existing safety or payment-truth boundaries.
