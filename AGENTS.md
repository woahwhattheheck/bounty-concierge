# Bounty work instructions

## No unpaid third-party bounty labor — owner instruction, 2026-09-20

Bryce directed: "We don't work for free. Make that end now."

- Do not perform or dispatch unpaid reviews, debugging, tests, patches, repair instructions, acceptance checks, or sign-off work for another claimant's bounty submission. An advertised bounty on their issue or PR is not compensation to Bryce/TJLabs.
- Bounty work must advance our own eligible claim/submission, an agreed paid review, or an explicit compensated collaboration. Verify the actual claimant/payee and payment route using the sponsor's required process. Do not infer entitlement from helping, a fork, a review, contributor credit, tips, exposure, points, or service credits.
- Keep the minimum payment facts in the existing work-order thread: sponsor amount, our claimant/payee, required claim/assignment, and submission/payment route. Do not create forms, peer permission gates, or new approval categories. Complete required claim/proposal steps promptly; avoid substantial implementation without an eligible collection route.
- Cancel outstanding unpaid third-party review/donor/validation assignments; preserve existing code, PRs, tests, and receipts. Redirect effort immediately to eligible cash bounties of at least $15 on green platforms or collection for completed work. Existing work orders do not override this instruction.
- Private inspection of competing work is permitted only where necessary to advance our own paid submission. Do not publish free consulting or build a competing contributor's acceptance package. Required tests, review fixes, and collaboration among our own agents on our own payable submissions continue.
- This applies to both owner GitHub accounts and all local/cloud seats. Root remains the sole GrantFox/Mova payment correspondent. No external cancellation or incident statement is authorized.

Canonical direction: https://tokenjunkielabs.slack.com/archives/C0BU51F1PL3/p1789921310879039
Copies: https://tokenjunkielabs.slack.com/archives/C0BTB4SUCP9/p1789921331105589 and https://tokenjunkielabs.slack.com/archives/C0BVANHNB26/p1789921332803679

Current minimum reward update, 2026-10-04: Bryce's latest fleet prompt permits $15 and up on green platforms, including the green BountyHub and GrantFox routes. This supersedes the earlier $25 and $50 minimums for new and existing eligible work. Actual claimant/payee eligibility, required sponsor steps, existing submission ownership, and payment-history evidence still apply. Historical reward observations and payment receipts retain their original amounts. Current direction is recorded in https://tokenjunkielabs.slack.com/archives/C0BU51F1PL3/p1791115167448889 ; the superseded $25 instruction remains at https://tokenjunkielabs.slack.com/archives/C0BU51F1PL3/p1791101910660469 .

## Payers with paid merges — owner instruction, 2026-10-04

Bryce directed: "Anyone who has paid in the past for merges is GREEN to submit to and work for."

- A payer with concrete evidence of an actual paid merge is GREEN even when the current offer is not funded or escrowed. Do not reject that payer's work solely for lacking advance funding.
- Link the payment/settlement evidence or authoritative paid record to the same payer and merged contribution in the existing work-order thread. A merged PR, advertised reward, points, or repository ownership alone does not establish payment history. Reuse recorded evidence; the first confirmed paid merge is sufficient and does not require another payer audit.
- The current offered reward must still be at least $15 on a green platform or qualified GREEN payer route. Current acceptance criteria, open work, our claimant/payee eligibility, required proposal/assignment steps, and existing submission ownership remain in force. This does not authorize unpaid work on another claimant's submission.
- Without actual paid-merge evidence, the existing funded/escrowed qualification still applies. Keep funded amounts, promised amounts, awards, and received cash distinct; a GREEN payer does not establish current funding, acceptance, or payment to us.

Canonical direction: https://tokenjunkielabs.slack.com/archives/C0BU51F1PL3/p1791104588390399


## Provider claim closeout serialization — swarm integrity, 2026-10-06

Live closeout races produced duplicate `/claim #N` comments even when two seats each performed a clean pre-write read. Treat provider claim linkage as a write-critical section, not as a search-only fence.

- Before posting a GrantFox/Algora-style GitHub issue `/claim #N` (or equivalent provider claim linkage), reserve the canonical GitHub issue with `python3 tools/swarm_claim_closeout.py owner/repo#N --owner <seat> --event-id <operation>`.
- Proceed only when the tool returns `status=CLAIM_READY` and `post_claim=true`. A `COLLISION` or `BLOCKED` result means do not emit the provider claim.
- After reservation succeeds, immediately re-read the provider issue comments and the canonical carrier head. If our claim already exists, do not write another one; release custody instead.
- Perform exactly one provider claim write, read it back, record the receipt, then release custody. Do not parallelize writes for the same issue.
- Use the canonical issue key emitted by the tool. Case, leading-zero, or prefix variants must not create distinct custody lanes.
- This guard serializes our own closeout writes only. It does not establish sponsor assignment, platform eligibility, funding, acceptance, award, or payment.

This is intentionally narrow: source builders, reviewers, and payout reconciliation keep their existing flows; only the provider claim mutation is serialized.
