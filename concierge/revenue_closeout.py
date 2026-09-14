# SPDX-License-Identifier: MIT
"""Authoritative paid-work closeout evaluator.

This layer composes the reviewed closeout implementation with a content-
generation fence for every mutable external feedback body. Settlement routing
never relies on GitHub's second-granularity timestamps to prove that a review,
inline review comment, or issue comment body was handled.

The module is read-only. It never contacts a sponsor, sends mail, mutates a
provider, or infers earned/paid cash from a merge.
"""

from __future__ import annotations

from typing import Any

import requests

from concierge import _revenue_closeout_public_v1 as _v1


# Preserve the reviewed public/test-facing surface; validation and scanning are
# replaced below. The preserved predecessor remains immutable reference code.
for _export_name in dir(_v1):
    if not _export_name.startswith("__"):
        globals()[_export_name] = getattr(_v1, _export_name)

_core = _v1._core

_FEEDBACK_BODY_ACK_KEYS = frozenset({"kind", "feedback_id", "body_sha256"})
_FEEDBACK_BODY_KINDS = frozenset({"review", "inline_comment", "comment"})
_MAX_FEEDBACK_BODY_ACKS = 1000


def _feedback_body_acknowledgements(
    raw: Any,
    legacy_review_acks: dict[int, str],
) -> dict[tuple[str, int], str]:
    """Normalize legacy review acks plus generalized feedback-body acks."""
    result: dict[tuple[str, int], str] = {
        ("review", review_id): digest
        for review_id, digest in legacy_review_acks.items()
    }
    if raw is None:
        return result
    if type(raw) is not list:
        raise RevenueCloseoutInputError(
            "acknowledged_feedback_bodies must be a list of exact feedback generations"
        )
    if len(raw) > _MAX_FEEDBACK_BODY_ACKS:
        raise RevenueCloseoutInputError("acknowledged_feedback_bodies is too large")

    seen_new: set[tuple[str, int]] = set()
    for entry in raw:
        if type(entry) is not dict or set(entry) != _FEEDBACK_BODY_ACK_KEYS:
            raise RevenueCloseoutInputError(
                "acknowledged_feedback_bodies entries require exact "
                "kind/feedback_id/body_sha256 keys"
            )
        kind = entry["kind"]
        feedback_id = entry["feedback_id"]
        digest = entry["body_sha256"]
        if type(kind) is not str or kind not in _FEEDBACK_BODY_KINDS:
            raise RevenueCloseoutInputError(
                "acknowledged_feedback_bodies.kind must be review, "
                "inline_comment, or comment"
            )
        if type(feedback_id) is not int or feedback_id <= 0:
            raise RevenueCloseoutInputError(
                "acknowledged_feedback_bodies.feedback_id must be a positive integer"
            )
        if type(digest) is not str or not _SHA256_RE.fullmatch(digest):
            raise RevenueCloseoutInputError(
                "acknowledged_feedback_bodies.body_sha256 must be lowercase SHA-256"
            )
        key = (kind, feedback_id)
        if key in seen_new:
            raise RevenueCloseoutInputError(
                "acknowledged_feedback_bodies must not repeat a feedback identity"
            )
        seen_new.add(key)
        prior = result.get(key)
        if prior is not None and prior != digest:
            raise RevenueCloseoutInputError(
                "legacy and generalized acknowledgements conflict for one feedback identity"
            )
        result[key] = digest
    return result


def _validate_item(raw: Any) -> dict[str, Any]:
    item = _v1._validate_item(raw)
    generalized = _feedback_body_acknowledgements(
        raw.get("acknowledged_feedback_bodies") if type(raw) is dict else None,
        item["acknowledged_review_bodies"],
    )
    return {
        **item,
        "acknowledged_feedback_bodies": generalized,
    }


def _feedback_body_generations(
    feedback: list[dict[str, Any]],
    acknowledgements: dict[tuple[str, int], str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition every actionable mutable body by exact stable-id generation."""
    acknowledged: list[dict[str, Any]] = []
    unacknowledged: list[dict[str, Any]] = []
    for event in feedback:
        kind = event.get("kind")
        if kind not in _FEEDBACK_BODY_KINDS or not event.get("_has_body"):
            continue
        if kind == "review" and event.get("state") != "COMMENTED":
            continue

        source_id = event.get("_source_id")
        if (
            not isinstance(source_id, tuple)
            or len(source_id) != 2
            or source_id[0] != kind
            or type(source_id[1]) is not int
            or source_id[1] <= 0
        ):
            raise RevenueCloseoutError(
                f"GitHub non-empty {kind} omitted a stable positive id"
            )
        digest = event.get("_body_digest")
        if type(digest) is not str or not _SHA256_RE.fullmatch(digest):
            raise RevenueCloseoutError(
                f"GitHub non-empty {kind} omitted a stable body digest"
            )

        receipt = {
            "kind": kind,
            "feedback_id": source_id[1],
            "body_sha256": digest,
            "author": event["author"],
            "at": _iso_or_none(event["at"]),
            "url": event["url"],
        }
        if acknowledgements.get((kind, source_id[1])) == digest:
            acknowledged.append(receipt)
        else:
            unacknowledged.append(receipt)
    return acknowledged, unacknowledged


def _legacy_review_receipts(
    receipts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "review_id": receipt["feedback_id"],
            "body_sha256": receipt["body_sha256"],
            "author": receipt["author"],
            "submitted_at": receipt["at"],
            "url": receipt["url"],
        }
        for receipt in receipts
        if receipt["kind"] == "review"
    ]


def _decorate_head_moved(
    result: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any]:
    result = _v1._decorate_head_moved(result, item)
    result["acknowledged_feedback_body_count"] = 0
    result["unacknowledged_feedback_body_count"] = 0
    result["unacknowledged_feedback_body_generations"] = []
    return result


def _source_key(event: dict[str, Any]) -> tuple[str, int] | None:
    source_id = event.get("_source_id")
    if (
        isinstance(source_id, tuple)
        and len(source_id) == 2
        and isinstance(source_id[0], str)
        and type(source_id[1]) is int
        and source_id[1] > 0
    ):
        return source_id[0], source_id[1]
    return None


def scan_paid_pr(
    raw_item: dict[str, Any],
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
) -> dict[str, Any]:
    """Read one paid-work PR and return a content-generation-safe closeout receipt."""
    item = _validate_item(raw_item)
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
        raise RevenueCloseoutInputError("max_pages must be positive")

    token = token or GITHUB_TOKEN
    headers = _headers(token)
    repo = item["repo"]
    number = item["pr"]

    initial_pr = _core._fetch_pr(repo, number, session=session, headers=headers)
    initial = _core._validate_pr_snapshot(initial_pr, item)
    expected_head = item["expected_head_sha"]
    if expected_head is not None and expected_head != initial["head_sha"]:
        return _decorate_head_moved(_core._head_moved_result(item, initial), item)

    if initial["state"] == "closed" or expected_head is not None:
        coherent = _core._coherent_lifecycle_snapshot(
            item,
            session=session,
            headers=headers,
            max_pages=max_pages,
        )
        if isinstance(coherent, dict):
            return _decorate_head_moved(coherent, item)
        snapshot, feedback = coherent
    else:
        snapshot = initial
        feedback = _core._collect_feedback(
            repo,
            number,
            item["operator_login"],
            session=session,
            headers=headers,
            max_pages=max_pages,
        )

    merged_at = snapshot["merged_at"]
    state = snapshot["state"]
    if merged_at is not None:
        safe_state = "MERGED"
    elif state == "closed":
        safe_state = "CLOSED_UNMERGED"
    else:
        safe_state = "OPEN"

    evidence = item["settlement_followup_evidence"]
    evidence_current = False
    if evidence is not None:
        _validate_contact_chronology(
            state=safe_state,
            merged_at=merged_at,
            evidence=evidence,
        )
        evidence_current = True

    review_decisions = _core._current_review_decisions(feedback)
    current_change_requests = [
        event
        for event in review_decisions.values()
        if event["state"] == "CHANGES_REQUESTED"
    ]
    last_seen = item["last_seen_at"]
    new_feedback = [event for event in feedback if event["at"] >= last_seen]

    acknowledged_bodies, unacknowledged_bodies = _feedback_body_generations(
        feedback,
        item["acknowledged_feedback_bodies"],
    )
    acknowledged_keys = {
        (receipt["kind"], receipt["feedback_id"])
        for receipt in acknowledged_bodies
    }

    response_feedback: list[dict[str, Any]] = []
    for event in new_feedback:
        kind = event["kind"]
        if kind in {"comment", "inline_comment"}:
            if event.get("_has_body") and _source_key(event) in acknowledged_keys:
                continue
            response_feedback.append(event)
        elif kind == "review" and event["state"] == "COMMENTED":
            if event.get("_has_body") and _source_key(event) in acknowledged_keys:
                continue
            response_feedback.append(event)

    unacknowledged_keys = {
        (receipt["kind"], receipt["feedback_id"])
        for receipt in unacknowledged_bodies
    }
    unacknowledged_events = [
        event for event in feedback if _source_key(event) in unacknowledged_keys
    ]

    if current_change_requests:
        next_action = "repair_requested"
        reason = "current_maintainer_changes_requested"
    elif unacknowledged_bodies:
        next_action = "respond_to_maintainer"
        kinds = {receipt["kind"] for receipt in unacknowledged_bodies}
        hidden_generation = any(event["at"] < last_seen for event in unacknowledged_events)
        if kinds == {"review"}:
            reason = "unacknowledged_maintainer_review_body_generation"
        elif hidden_generation:
            reason = "unacknowledged_maintainer_feedback_body_generation"
        else:
            # Preserve the historical reason for newly visible comment feedback;
            # the generation receipt still prevents timestamp-only acknowledgement.
            reason = "new_maintainer_feedback"
    elif response_feedback:
        next_action = "respond_to_maintainer"
        reason = "new_maintainer_feedback"
    elif merged_at is not None:
        if evidence_current:
            next_action = "monitor_settlement"
            reason = "merged_followup_send_evidenced"
        else:
            next_action = "route_settlement_followup"
            if item["settlement_followup_url"] is None:
                reason = "merged_without_settlement_route"
            else:
                reason = "merged_route_metadata_without_send_evidence"
    elif state == "closed":
        next_action = "investigate_closed_unmerged"
        reason = "pr_closed_unmerged"
    elif new_feedback:
        next_action = "await_acceptance"
        reason = "new_nonactionable_maintainer_review"
    else:
        next_action = "await_acceptance"
        reason = "open_without_new_maintainer_feedback"

    if next_action == "respond_to_maintainer" and unacknowledged_bodies:
        action_events = unacknowledged_events + response_feedback
        latest = max(action_events, key=lambda event: event["at"]) if action_events else None
    else:
        latest = new_feedback[-1] if new_feedback else None

    acknowledged_reviews = _legacy_review_receipts(acknowledged_bodies)
    unacknowledged_reviews = _legacy_review_receipts(unacknowledged_bodies)

    return {
        "repo": repo,
        "pr": number,
        "canonical_url": snapshot["canonical_url"],
        "head_sha": snapshot["head_sha"],
        "advertised_amount": format(item["advertised_amount"], "f"),
        "currency": item["currency"],
        "state": safe_state,
        "merged_at": _iso_or_none(merged_at),
        "next_action": next_action,
        "reason": reason,
        "new_feedback_count": len(new_feedback),
        "current_change_request_count": len(current_change_requests),
        "latest_feedback": _latest_feedback_receipt(latest),
        "settlement_followup_url": item["settlement_followup_url"],
        "settlement_followup_send_evidenced": evidence_current,
        "settlement_followup_evidence": evidence,
        "settlement_route_proves_prior_contact": False,
        "acknowledged_review_body_count": len(acknowledged_reviews),
        "unacknowledged_review_body_count": len(unacknowledged_reviews),
        "unacknowledged_review_body_generations": unacknowledged_reviews,
        "acknowledged_feedback_body_count": len(acknowledged_bodies),
        "unacknowledged_feedback_body_count": len(unacknowledged_bodies),
        "unacknowledged_feedback_body_generations": unacknowledged_bodies,
        "cash_status": "not_inferred",
    }


# Install the single authoritative evaluator into the compatibility facade.
# Its queue/CLI functions resolve these globals dynamically, so every public
# path receives the same content-generation fence. The preserved predecessor is
# not patched, preventing validation recursion and retaining auditability.
_core._validate_item = _validate_item
_core.scan_paid_pr = scan_paid_pr


if __name__ == "__main__":
    raise SystemExit(main())
