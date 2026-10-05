# SPDX-License-Identifier: MIT
from concierge.work_order_lease import _compare_projections


def _projection(*, completed, semantic="a", linked_prs=None, generation=None):
    return {
        "repo": "owner/repo",
        "number": 7,
        "submission_target": None,
        "policy": {"max_pages": 10, "saturation_threshold": 5},
        "assignment": {
            "formal_assignee_count": 0,
            "assigned_to_operator": False,
            "foreign_assignee_count": 0,
            "principal_check": "NO_ASSIGNEES",
        },
        "generation": generation or {"issue": "g1"},
        "authority": {"maintainer_comments": []},
        "canonical_audit": {
            "issue_state": "open",
            "open_pr_count": len(linked_prs or []),
            "stale_listing_signal": False,
            "search_truncated": False,
        },
        "linked_prs": linked_prs or [],
        "qualification": {
            "dispatch": True,
            "disposition": "ACCEPT",
            "reason_codes": [],
        },
        "capture_receipt_sha256": "1" * 64,
        "source_generation_sha256": semantic * 64,
        "observed_at": completed,
        "completed_at": completed,
    }


def test_later_equivalent_capture_keeps_lease_ready():
    original = _projection(completed="2026-10-04T20:00:00Z")
    refreshed = _projection(completed="2026-10-04T20:01:00Z")

    result = _compare_projections(original, refreshed)

    assert result["status"] == "READY"
    assert result["dispatch"] is True
    assert result["reason_codes"] == []


def test_new_linked_pr_invalidates_work_order():
    original = _projection(completed="2026-10-04T20:00:00Z")
    refreshed = _projection(
        completed="2026-10-04T20:01:00Z",
        semantic="b",
        linked_prs=[{
            "repository": "owner/repo",
            "number": 11,
            "author_login": "peer",
            "head_repository": "peer/repo",
            "head_ref": "fix",
            "head_sha": "2" * 40,
        }],
    )

    result = _compare_projections(original, refreshed)

    assert result["status"] == "STALE"
    assert result["dispatch"] is False
    assert "COMPETITION_CHANGED" in result["reason_codes"]


def test_same_capture_is_not_a_refresh():
    original = _projection(completed="2026-10-04T20:00:00Z")
    result = _compare_projections(original, dict(original))

    assert result["status"] == "STALE"
    assert result["reason_codes"] == ["REFRESH_NOT_NEWER"]


def test_unknown_semantic_delta_fails_closed():
    original = _projection(completed="2026-10-04T20:00:00Z")
    refreshed = _projection(completed="2026-10-04T20:01:00Z", semantic="b")

    result = _compare_projections(original, refreshed)

    assert result["status"] == "STALE"
    assert result["reason_codes"] == ["SOURCE_GENERATION_CHANGED"]
