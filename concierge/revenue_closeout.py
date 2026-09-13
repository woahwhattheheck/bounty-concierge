# SPDX-License-Identifier: MIT
"""Paid-work closeout with evidence-bound settlement-contact state.

The historical closeout implementation is preserved byte-for-byte in
``revenue_closeout_core``.  This public module closes one authority gap in that
implementation: a configured ``settlement_followup_url`` is only route metadata;
it does not prove that a collection/follow-up message was sent.

A merged item therefore remains actionable until an explicit send receipt is
bound to the same repository, PR, exact settlement route, and post-merge time.
The receipt is evidence supplied to this read-only compiler; this module never
contacts a sponsor, sends mail, mutates a provider, or infers cash/revenue.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Any

from concierge import revenue_closeout_core as _core


# Preserve the established public/test-facing API.  Only the validation and
# closeout decision seams below are replaced.
for _export_name in dir(_core):
    if not _export_name.startswith("__"):
        globals()[_export_name] = getattr(_core, _export_name)


_original_validate_item = _core._validate_item
_original_scan_paid_pr = _core.scan_paid_pr

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
    if raw["repo"] != repo or raw["pr"] != pr:
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


def _validate_item(raw: Any) -> dict[str, Any]:
    item = _original_validate_item(raw)
    evidence = _contact_evidence(
        raw.get("settlement_followup_evidence") if type(raw) is dict else None,
        repo=item["repo"],
        pr=item["pr"],
        settlement_route=item["settlement_followup_url"],
    )
    return {**item, "settlement_followup_evidence": evidence}


def _validate_contact_chronology(
    result: dict[str, Any], evidence: dict[str, Any]
) -> None:
    if result["state"] != "MERGED":
        raise RevenueCloseoutInputError(
            "settlement_followup_evidence is only valid for currently merged work"
        )
    merged_at = _parse_timestamp(
        result.get("merged_at"), field="merged_at", allow_none=False
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
    if sent_at > now + timedelta(minutes=5):
        raise RevenueCloseoutInputError(
            "settlement follow-up send evidence cannot be in the future"
        )


def scan_paid_pr(
    raw_item: dict[str, Any],
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
) -> dict[str, Any]:
    """Read one paid-work PR without confusing a route with proof of contact."""
    item = _validate_item(raw_item)

    # The core scanner resolves its validator dynamically.  Keep it pointed at
    # this stricter validator so direct and queue callers share one contract.
    _core._validate_item = globals()["_validate_item"]
    result = _original_scan_paid_pr(
        raw_item,
        token,
        session=session,
        max_pages=max_pages,
    )

    evidence = item["settlement_followup_evidence"]
    if evidence is not None and result["state"] != "HEAD_MOVED":
        _validate_contact_chronology(result, evidence)

    # Maintainer repair/response continues to outrank settlement routing.  Only
    # the core's settlement decision pair is refined here.
    if result["state"] == "MERGED" and result["next_action"] in {
        "route_settlement_followup",
        "monitor_settlement",
    }:
        if evidence is None:
            result["next_action"] = "route_settlement_followup"
            if result.get("settlement_followup_url") is None:
                result["reason"] = "merged_without_settlement_route"
            else:
                result["reason"] = "merged_route_metadata_without_send_evidence"
        else:
            result["next_action"] = "monitor_settlement"
            result["reason"] = "merged_followup_send_evidenced"

    result["settlement_followup_send_evidenced"] = evidence is not None
    result["settlement_followup_evidence"] = evidence
    result["settlement_route_proves_prior_contact"] = False
    return result


# Core-defined functions resolve collaborators through the core module's global
# namespace.  Patch the two guarded seams so build_closeout_queue() and main()
# automatically use the evidence-bound behavior while preserving their APIs.
_core._validate_item = _validate_item
_core.scan_paid_pr = scan_paid_pr


if __name__ == "__main__":
    raise SystemExit(main())
