# SPDX-License-Identifier: MIT
from concierge.work_order_lease import _compare_projections, reconcile_tracked_head


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


def test_new_envelope_with_old_observation_is_not_fresh_evidence():
    original = _projection(completed="2026-10-04T20:00:00Z")
    refreshed = _projection(completed="2026-10-04T20:01:00Z")
    # A cached provider read was processed later without collecting a new
    # issue/PR/assignment snapshot. The late completion is insufficient.
    refreshed["observed_at"] = original["observed_at"]

    result = _compare_projections(original, refreshed)
    assert result["status"] == "STALE"
    assert result["dispatch"] is False
    assert "REFRESH_OBSERVATION_NOT_NEWER" in result["reason_codes"]


def test_regressed_or_future_observation_fails_closed():
    original = _projection(completed="2026-10-04T20:00:00Z")
    refreshed = _projection(completed="2026-10-04T20:01:00Z")
    refreshed["observed_at"] = "2026-10-04T19:59:00Z"

    stale = _compare_projections(original, refreshed)
    assert "REFRESH_OBSERVATION_NOT_NEWER" in stale["reason_codes"]

    refreshed["observed_at"] = "2026-10-04T20:02:00Z"
    invalid = _compare_projections(original, refreshed)
    assert invalid["status"] == "STALE"
    assert "REFRESH_OBSERVATION_AFTER_COMPLETION" in invalid["reason_codes"]


def test_unknown_semantic_delta_fails_closed():
    original = _projection(completed="2026-10-04T20:00:00Z")
    refreshed = _projection(completed="2026-10-04T20:01:00Z", semantic="b")

    result = _compare_projections(original, refreshed)

    assert result["status"] == "STALE"
    assert result["reason_codes"] == ["SOURCE_GENERATION_CHANGED"]


def _head_reconciliation(**overrides):
    values = {
        "operation_id": "op-123",
        "repo": "owner/repo",
        "branch": "fix/example",
        "expected_head": "a" * 40,
        "observed_head": "b" * 40,
        "claimed_paths": ["src/feature.py", "tests/test_feature.py"],
        "touched_paths": ["src/feature.py", "tests/test_feature.py"],
        "owner": "seat-a",
        "source": "slack-work-order-123",
        "provider_readback": {"provider": "github", "compare": "a..b"},
    }
    values.update(overrides)
    return reconcile_tracked_head(**values)


def test_head_reconciliation_tombstones_exact_scope_completion():
    result = _head_reconciliation()

    assert result["status"] == "COLLISION_RECONCILIATION"
    assert result["classification"] == "CLAIMED_SCOPE_COMPLETE"
    assert result["action"] == "TOMBSTONE_DUPLICATE"
    assert result["dispatch"] is False
    assert result["tombstone"] is True
    assert result["next_expected_head"] == "b" * 40
    assert result["overlap_paths"] == ["src/feature.py", "tests/test_feature.py"]


def test_head_reconciliation_rebases_orthogonal_advance():
    result = _head_reconciliation(
        touched_paths=["docs/README.md"],
        provider_readback={"provider": "github", "compare": "orthogonal"},
    )

    assert result["status"] == "READY"
    assert result["classification"] == "ORTHOGONAL_ADVANCE"
    assert result["action"] == "REBASE_EXPECTED_HEAD"
    assert result["dispatch"] is True
    assert result["rebase"] is True
    assert result["next_expected_head"] == "b" * 40
    assert result["overlap_paths"] == []


def test_head_reconciliation_keeps_true_stale_lease_when_head_is_unchanged():
    result = _head_reconciliation(
        observed_head="a" * 40,
        touched_paths=[],
        provider_readback={"provider": "github", "head": "a" * 40},
    )

    assert result["status"] == "STALE"
    assert result["classification"] == "UNCHANGED_STALE"
    assert result["action"] == "RETAIN_STALE_LEASE"
    assert result["dispatch"] is False
    assert result["rebase"] is False
    assert result["tombstone"] is False
