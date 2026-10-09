# Explicit test/research bounty targets — payment-risk preflight

**Status:** actual admission policy. This is a paid-work BLOCK, not a fraud allegation. Screening never creates GREEN/payer proof.

## Owner-source incident

The owner-authored [README](https://github.com/ApexOpsStudio/ai-gitops-test-target/blob/main/README.md) for `ApexOpsStudio/ai-gitops-test-target` explicitly calls it a *test target repository — not a real project*, created to validate an AI-assisted bounty workflow. The retained Git blob is `91da9a7d29915af0508a0a90e29cff05760b9856`. First-party [issues #1](https://github.com/ApexOpsStudio/ai-gitops-test-target/issues/1), [#2](https://github.com/ApexOpsStudio/ai-gitops-test-target/issues/2), and [#3](https://github.com/ApexOpsStudio/ai-gitops-test-target/issues/3) use `/bounty 50`, `/bounty 75`, `/bounty 100` even though the README disclaims real work. Issue #1's `Bounty` label description identifies it as a **test bounty issue**. The amounts are simulation labels, NOT receivables.

## Execution

`concierge.test_target_admission.screen_paid_target(owner_repo)` checks the exact-retained known-target list without network calls. An optional README observation must be an actual owner-repo GitHub README path plus byte-matching Git blob SHA; explicit strong self-disclosure returns `BLOCK_SELF_DISCLOSED_NONPAYABLE_TARGET`. Other documentation's ordinary words "test" or "research" do not cause an automatic conviction. **No block match never equals a payer GREEN.** The cash-intake pathway adds a fail-closed row before handing an otherwise qualifying cash issue to work scheduling.

For each newly discovered sponsor, independently verify actual previous payments for merged work by the **same payer**, current activity and the exact task's live settlement path. Ambiguous source, no payout evidence or unresolved accepted work -> `HOLD_UNVERIFIED`; sponsor statement confirming a simulation -> `BLOCK_SELF_DISCLOSED_NONPAYABLE_TARGET`; actual overdue nonpayment/refusal after accepted work and due notice -> investigate `BLACKLISTED` with exact receipts. Do not equate PR merge, posted `/claim`, label dollars or marketplace green status with money received. Preserve outstanding original-author claims on existing work. Keep internal evidence; do not accuse outside maintainers of fraud without substantiation.

## Updating

When a new test target is confirmed by its owner README, add exact `owner/repo`, owner README URL and SHA to `policies/self_disclosed_test_targets_v1.json` with evidence rationale. Re-check historical decisions if the owner's repo README changes; do not automatically clear an existing hold from transient missing README or a server error.
