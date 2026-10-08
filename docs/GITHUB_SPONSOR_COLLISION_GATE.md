# Live sponsor issue-carrier collision fence

The original-author bounty PR publisher uses the new concierge.github_sponsor_collision_gate.publish_sponsor_issue_pr adapter before invoking its existing authorized write transport. This is one usable entrypoint around the shared GitHub cooldown/recovery/route breaker, not a second raw publisher.

## Mandatory first-party reads, same original-author token

1. GET /user: exact expected actor and numeric ID, which for woahwhattheheck is 293286387.
2. GET /repos/{owner}/{repo}: exact destination and Boolean archived flag. Archived destination blocks before any PR-list or write request.
3. GET /repos/{owner}/{repo}/pulls?state=all&per_page=100&page=N: inspect title, full body, exact URL, user, source head and state across the complete bounded pagination. Fail closed if the cap is reached before a terminal page.

Explicit Closes/Fixes/Refs/claim issue keys, canonical issue URLs, related-only #N references, and original-author identical commit heads all cause review holds. Cross-author and same-author carrier collisions are recorded. Unknown overlap is not treated as proof of completion and cannot authorize a duplicate bounty PR.

First-party HTTP 403 or 429, missing/ambiguous repository or actor identity, malformed PR response, and truncated pagination fail closed without trying alternate credentials. A colliding issue produces a SHA-256 digested HOLD_EXISTING_ISSUE_CARRIER receipt including canonical PR links, issuer, source head and observed timestamp, with provider_write_called=false. No hit passes to the existing provider-reconcile and shared cooldown admission; after allowed admission the supplied transport is the only code path that mutates GitHub.

## Integrating the actual original-author PR transport

Call publish_sponsor_issue_pr instead of generic execute_publish_operation for a new issue-bound sponsor PR. Supply the existing authorized original-author token, the exact sponsor issue, original branch, expected head, shared rail metadata, and existing one-shot authorized transport closure. The closure MUST use the same actor/token verified by GET /user. No credential is printed, created, or switched. Example:

    from concierge.github_sponsor_collision_gate import publish_sponsor_issue_pr
    receipt = publish_sponsor_issue_pr(
        ledger_path=existing_shared_cooldown_path,
        token=existing_original_user_token,
        rail="github-original-user-token",
        actor="woahwhattheheck",
        actor_id=293286387,
        operation=stable_operation_id,
        repo="sponsor/repository",
        issue_number=123,
        carrier=original_branch,
        expected_head=source_commit_sha,
        transport=lambda: original_token_create_pr_once(),
        cooldown_scope=existing_write_scope,
    )

A new PR provider write 422/403 is ambiguous; perform a fresh upstream issue/carrier census before considering any original-owner authorized retry. This wrapper is best-effort live read, not a distributed atomic lock across independently posting agents. Preserve the original source and provider status; a HOLD is not a claim submission, payout or acceptance.

Focused contract checks: python -m unittest tests.test_github_sponsor_collision_gate. These fake provider reads cover cross-author issue claims, duplicate original heads, clean/archived repository, and rate-limit closure. No live network execution is represented by these test fixtures. Official first-party PR create/readback must confirm real publication.
