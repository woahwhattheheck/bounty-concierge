# SPDX-License-Identifier: MIT
"""Focused admission regression: payment proof is necessary, never sufficient."""
from copy import deepcopy
from concierge.bountyhub_canonical_admission import AdmissionError, assess, SCHEMA


def baseline():
    return {
        "schema": SCHEMA, "as_of": "2026-10-09T08:30:00Z",
        "listing": {
            "id": "example-listing", "issue_url": "https://github.com/example/repo/issues/42",
            "advertised_usd": "75.00", "funding_type": "escrowed",
        },
        "github": {
            "observed_at": "2026-10-09T08:20:00Z",
            "issue_url": "https://github.com/example/repo/issues/42", "state": "open",
            "repository_archived": False, "last_maintainer_action_at": "2026-10-08T10:00:00Z",
            "assignees": [], "linked_open_pr_urls": [], "linked_pr_census_complete": True,
        },
        "payer": {
            "sponsor_name": "Example", "proof_sponsor_name": "Example",
            "completed_paid_merge_verified": True,
            "receipt_url": "https://opencollective.com/example/expenses/123",
        },
    }


def run():
    ready = assess(baseline())
    assert ready["decision"] == "READY_FOR_NEW_BUILD", ready

    contested = baseline()
    contested["github"]["linked_open_pr_urls"] = ["https://github.com/example/repo/pull/55"]
    contested["github"]["assignees"] = ["assigned-dev"]
    result = assess(contested)
    assert result["decision"] == "HOLD"
    assert "EXISTING_OPEN_IMPLEMENTATION" in result["reason_codes"]
    assert "ASSIGNED_TO_EXISTING_CONTRIBUTOR" in result["reason_codes"]

    stale_card = baseline()
    stale_card["github"]["issue_url"] = "https://github.com/example/repo/issues/43"
    stale_card["github"]["state"] = "closed"
    stale_card["payer"]["completed_paid_merge_verified"] = False
    stale_card["github"]["observed_at"] = "2026-10-01T01:00:00Z"
    result = assess(stale_card)
    assert set(("CANONICAL_ISSUE_MISMATCH", "CANONICAL_ISSUE_CLOSED",
                "SAME_SPONSOR_COMPLETED_PAID_MERGE_UNVERIFIED", "CANONICAL_SOURCE_STALE")).issubset(result["reason_codes"])

    bad_pr = deepcopy(baseline())
    bad_pr["github"]["linked_open_pr_urls"] = ["https://other-site.example/owner/repo/pull/6"]
    try:
        assess(bad_pr)
    except AdmissionError:
        pass
    else:
        raise AssertionError("non-first-party source URL accepted")

    # A dead repository cannot bypass the policy with a fabricated
    # escrow card, active GitHub snapshot and paid-merge flag.
    dead = baseline()
    dead_url = "https://github.com/Claude-Builders-Bounty/claude-builders-bounty/issues/4"
    dead["listing"]["issue_url"] = dead_url
    dead["github"]["issue_url"] = dead_url
    result = assess(dead)
    assert result["decision"] == "HOLD", result
    assert "REPO_KNOWN_DEAD" in result["reason_codes"], result


    # Explicit first-party nonpayable programs must HOLD even when a
    # marketplace card says escrowed and a forged payout field says verified.
    # Prior original claims/PRs are not changed by an intake-only decision.
    for repo_slug in (
        "UnsafeLabs/Bounty-Hunters",
        "ApexOpsStudio/ai-gitops-test-target",
        "golemcloud/golem-ai",
    ):
        excluded = baseline()
        url = f"https://github.com/{repo_slug}/issues/42"
        excluded["listing"]["issue_url"] = url
        excluded["github"]["issue_url"] = url
        decision = assess(excluded)
        assert decision["decision"] == "HOLD", (repo_slug, decision)
        assert "REPO_PROGRAM_NONPAYABLE" in decision["reason_codes"], (repo_slug, decision)
        assert decision["paid_to_original_claimant"] is False
        assert decision["claim_or_submission_performed"] is False

    print("8 focused admission cases passed")


if __name__ == "__main__":
    run()
