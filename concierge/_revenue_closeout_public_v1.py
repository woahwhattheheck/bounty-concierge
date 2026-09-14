# SPDX-License-Identifier: MIT
"""Paid-work closeout with coherent feedback and evidence-bound settlement state.

The historical scanner lives in ``revenue_closeout_core``.  This public module
keeps its provider-coherence machinery while closing two authority boundaries:

* a mutable, non-empty COMMENTED review summary stays actionable until the
  operator acknowledges the exact ``(review_id, body_sha256)`` generation; and
* a configured settlement route is metadata only, never proof that collection
  or follow-up contact was actually sent.

The module is read-only.  It never contacts a sponsor, sends mail, mutates a
provider, or infers earned/paid cash from a merge.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Any

import requests

from concierge import revenue_closeout_core as _core


# Preserve the established public/test-facing API.  The guarded validation and
# scan seams below are replaced; queue/CLI helpers continue to resolve them
# through the core module's global namespace.
for _export_name in dir(_core):
    if not _export_name.startswith("__"):
        globals()[_export_name] = getattr(_core, _export_name)


_original_validate_item = _core._validate_item

_CONTACT_KEYS = frozenset(
    {
        "repo",
        "pr",
        "provider",
        "receipt_ref",
        "receipt_sha256",
        "sent_at",
        "settlement_route_sha256",
    }
)
_REVIEW_BODY_ACK_KEYS = frozenset({"review_id", "body_sha256"})
_MAX_REVIEW_BODY_ACKS = 1000
_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RECEIPT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _route_sha256(route: str) -> str:
    return hashlib.sha256(route.encode("utf-8")).hexdigest()


def _contact_evidence(
    raw: Any,
    *,
    repo: str,
    pr: int,
    settlement_route: str | None,
) -> dict[str, Any] | None:
    if raw is None:
        return None
    if type(raw) is not dict or set(raw) != _CONTACT_KEYS:
        raise RevenueCloseoutInputError(
            "settlement_followup_evidence must contain exact receipt-binding keys"
        )
    if type(raw["repo"]) is not str or raw["repo"] != repo:
        raise RevenueCloseoutInputError(
            "settlement_followup_evidence cannot be transplanted across PR identities"
        )
    if type(raw["pr"]) is not int or raw["pr"] != pr:
        raise RevenueCloseoutInputError(
            "settlement_followup_evidence cannot be transplanted across PR identities"
        )
    if settlement_route is None:
        raise RevenueCloseoutInputError(
            "settlement_followup_evidence requires settlement_followup_url route metadata"
        )

    provider = raw["provider"]
    if type(provider) is not str or not _PROVIDER_RE.fullmatch(provider):
        raise RevenueCloseoutInputError(
            "settlement_followup_evidence.provider must be a bounded provider token"
        )
    receipt_ref = raw["receipt_ref"]
    if type(receipt_ref) is not str or not _RECEIPT_REF_RE.fullmatch(receipt_ref):
        raise RevenueCloseoutInputError(
            "settlement_followup_evidence.receipt_ref must be one bounded opaque reference"
        )
    receipt_sha = raw["receipt_sha256"]
    if type(receipt_sha) is not str or not _SHA256_RE.fullmatch(receipt_sha):
        raise RevenueCloseoutInputError(
            "settlement_followup_evidence.receipt_sha256 must be lowercase SHA-256"
        )
    route_sha = raw["settlement_route_sha256"]
    if type(route_sha) is not str or not _SHA256_RE.fullmatch(route_sha):
        raise RevenueCloseoutInputError(
            "settlement_followup_evidence.settlement_route_sha256 must be lowercase SHA-256"
        )
    expected_route_sha = _route_sha256(settlement_route)
    if route_sha != expected_route_sha:
        raise RevenueCloseoutInputError(
            "settlement_followup_evidence is not bound to the configured settlement route"
        )
    sent_at = _parse_timestamp(
        raw["sent_at"],
        field="settlement_followup_evidence.sent_at",
        allow_none=False,
    )
    return {
        "repo": repo,
        "pr": pr,
        "provider": provider,
        "receipt_ref": receipt_ref,
        "receipt_sha256": receipt_sha,
        "sent_at": _iso_or_none(sent_at),
        "settlement_route_sha256": route_sha,
    }


def _review_body_acknowledgements(raw: Any) -> dict[int, str]:
    if raw is None:
        return {}
    if type(raw) is not list:
        raise RevenueCloseoutInputError(
            "acknowledged_review_bodies must be a list of exact review generations"
        )
    if len(raw) > _MAX_REVIEW_BODY_ACKS:
        raise RevenueCloseoutInputError("acknowledged_review_bodies is too large")

    result: dict[int, str] = {}
    for entry in raw:
        if type(entry) is not dict or set(entry) != _REVIEW_BODY_ACK_KEYS:
            raise RevenueCloseoutInputError(
                "acknowledged_review_bodies entries require exact review_id/body_sha256 keys"
            )
        review_id = entry["review_id"]
        digest = entry["body_sha256"]
        if type(review_id) is not int or review_id <= 0:
            raise RevenueCloseoutInputError(
                "acknowledged_review_bodies.review_id must be a positive integer"
            )
        if type(digest) is not str or not _SHA256_RE.fullmatch(digest):
            raise RevenueCloseoutInputError(
                "acknowledged_review_bodies.body_sha256 must be lowercase SHA-256"
            )
        if review_id in result:
            raise RevenueCloseoutInputError(
                "acknowledged_review_bodies must not repeat review_id"
            )
        result[review_id] = digest
    return result


def _validate_item(raw: Any) -> dict[str, Any]:
    item = _original_validate_item(raw)
    evidence = _contact_evidence(
        raw.get("settlement_followup_evidence") if type(raw) is dict else None,
        repo=item["repo"],
        pr=item["pr"],
        settlement_route=item["settlement_followup_url"],
    )
    review_acks = _review_body_acknowledgements(
        raw.get("acknowledged_review_bodies") if type(raw) is dict else None
    )
    return {
        **item,
        "settlement_followup_evidence": evidence,
        "acknowledged_review_bodies": review_acks,
    }


def _validate_contact_chronology(
    *,
    state: str,
    merged_at: datetime | None,
    evidence: dict[str, Any],
) -> None:
    if state != "MERGED" or merged_at is None:
        raise RevenueCloseoutInputError(
            "settlement_followup_evidence is only valid for currently merged work"
        )
    sent_at = _parse_timestamp(
        evidence["sent_at"],
        field="settlement_followup_evidence.sent_at",
        allow_none=False,
    )
    now = datetime.now(timezone.utc)
    if sent_at < merged_at:
        raise RevenueCloseoutInputError(
            "settlement follow-up send evidence cannot predate the merge"
        )
    if sent_at > now:
        raise RevenueCloseoutInputError(
            "settlement follow-up send evidence cannot be in the future"
        )


def _review_body_generations(
    feedback: list[dict[str, Any]],
    acknowledgements: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition mutable COMMENTED review summaries by exact acknowledged body."""
    acknowledged: list[dict[str, Any]] = []
    unacknowledged: list[dict[str, Any]] = []
    for event in feedback:
        if (
            event["kind"] != "review"
            or event["state"] != "COMMENTED"
            or not event.get("_has_body")
        ):
            continue
        source_id = event.get("_source_id")
        if (
            not isinstance(source_id, tuple)
            or len(source_id) != 2
            or source_id[0] != "review"
            or type(source_id[1]) is not int
            or source_id[1] <= 0
        ):
            raise RevenueCloseoutError(
                "GitHub non-empty COMMENTED review omitted a stable positive id"
            )
        digest = event.get("_body_digest")
        if type(digest) is not str or not _SHA256_RE.fullmatch(digest):
            raise RevenueCloseoutError(
                "GitHub non-empty COMMENTED review omitted a stable body digest"
            )
        receipt = {
            "review_id": source_id[1],
            "body_sha256": digest,
            "author": event["author"],
            "submitted_at": _iso_or_none(event["at"]),
            "url": event["url"],
        }
        if acknowledgements.get(source_id[1]) == digest:
            acknowledged.append(receipt)
        else:
            unacknowledged.append(receipt)
    return acknowledged, unacknowledged


def _latest_feedback_receipt(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "kind": event["kind"],
        "state": event["state"],
        "author": event["author"],
        "at": _iso_or_none(event["at"]),
        "url": event["url"],
    }


def _decorate_head_moved(
    result: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any]:
    result = dict(result)
    result["settlement_followup_send_evidenced"] = False
    result["settlement_followup_evidence"] = item["settlement_followup_evidence"]
    result["settlement_route_proves_prior_contact"] = False
    result["acknowledged_review_body_count"] = 0
    result["unacknowledged_review_body_count"] = 0
    result["unacknowledged_review_body_generations"] = []
    return result


def scan_paid_pr(
    raw_item: dict[str, Any],
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
) -> dict[str, Any]:
    """Read one paid-work PR and return a fail-closed closeout action receipt."""
    item = _validate_item(raw_item)
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
        raise RevenueCloseoutInputError("max_pages must be positive")

    token = token or GITHUB_TOKEN
    headers = _headers(token)
    repo = item["repo"]
    number = item["pr"]

    # Preserve #91's risk-weighted coherence contract: obvious expected-head
    # movement is cheap; closed PRs and every exact-head scan require repeated
    # PR + normalized-feedback generations before lifecycle/settlement routing.
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

    acknowledged_bodies, unacknowledged_bodies = _review_body_generations(
        feedback,
        item["acknowledged_review_bodies"],
    )
    response_feedback = [
        event
        for event in new_feedback
        if event["kind"] in {"comment", "inline_comment"}
        or (
            event["kind"] == "review"
            and event["state"] == "COMMENTED"
            and not event.get("_has_body")
        )
    ]

    if current_change_requests:
        next_action = "repair_requested"
        reason = "current_maintainer_changes_requested"
    elif unacknowledged_bodies:
        next_action = "respond_to_maintainer"
        reason = "unacknowledged_maintainer_review_body_generation"
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
        unack_ids = {entry["review_id"] for entry in unacknowledged_bodies}
        action_events = [
            event
            for event in feedback
            if (
                event["kind"] == "review"
                and isinstance(event.get("_source_id"), tuple)
                and len(event["_source_id"]) == 2
                and event["_source_id"][0] == "review"
                and event["_source_id"][1] in unack_ids
            )
        ] + response_feedback
        latest = max(action_events, key=lambda event: event["at"]) if action_events else None
    else:
        latest = new_feedback[-1] if new_feedback else None

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
        "acknowledged_review_body_count": len(acknowledged_bodies),
        "unacknowledged_review_body_count": len(unacknowledged_bodies),
        "unacknowledged_review_body_generations": unacknowledged_bodies,
        "cash_status": "not_inferred",
    }


# Core-defined queue/CLI functions resolve these collaborators through the core
# module's global namespace.  Patch only the guarded seams; provider transport
# remains caller-supplied/read-only exactly as before.
_core._validate_item = _validate_item
_core.scan_paid_pr = scan_paid_pr


if __name__ == "__main__":
    raise SystemExit(main())
