# Bounty claim publication text admission

Revenue-producing PRs must request their compensation affirmatively, without inadvertently disclaiming the bounty or original contributor claim. This is a narrow pre-publication safeguard, not an eligibility or payout verifier.

Before any provider read/write, call execute_publish_operation with the exact proposed PR or claim comment copy in bounty_claim_body, and bounty_claim_required=True for sponsor bounty carriers.

Example (authorized publish call):

    execute_publish_operation(path, token, rail=rail, actor=actor,
        operation=operation, action="create-pull-request",
        repo=repo, carrier=carrier, expected_head=head,
        transport=provider_write, provider_repository_state=repo_read,
        provider_reconcile=all_state_pr_read,
        bounty_claim_body=proposed_body, bounty_claim_required=True)

Acceptable affirmative wording: Fixes #742. /claim #742. I affirmatively request the advertised bounty payment under its original terms.

The optional preflight returns BOUNTY_CLAIM_TEXT_BLOCKED plus stable finding_codes for missing copy, explicit waiver language, and an absent affirmative compensation request. It does not invoke provider repository reads, provider PR census, or write transport in those cases. It does not change the original author, published claims, claim registrations, or other already-submitted carrier metadata.

Evidence boundaries about not having executed tests are not bounty waivers; the object must be payment, reward, bounty or compensation. A slash-claim command alone does not establish an affirmative payment request. A contradictory waiver remains blocking even beside a positive request.

Important: The guard is opt-in and only protects call sites that pass bounty_claim_required=True and the exact proposed body. Do not represent it as platform-wide enforcement or a sponsor payout approval. Preserve platform registration and provider eligibility checks. Do not alter any existing PR body without original-author permission and a fresh exact-head read.

Only directly relevant focused validation if necessary: pytest -q tests/test_bounty_claim_text.py