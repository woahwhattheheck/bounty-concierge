# SPDX-License-Identifier: MIT
"""Owner-curated, offline same-payer paid-merge gate for NEW bounty labor.

Input is the owner's REPOSITORY_WORK_ELIGIBILITY registry, not untrusted issue
text, a marketplace card, a slash claim, or a merged PR alone. The registry
must be refreshed and its public payout links independently checked upstream.
This is a new-work gate only: it never withdraws existing submissions or claims.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any
from urllib.parse import urlsplit

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_PR = re.compile(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*\Z")
_RECEIPT_HOSTS = frozenset({"opencollective.com", "algora.io", "console.algora.io"})
MAX_SNAPSHOT_AGE = timedelta(hours=72)
MAX_MAINTAINER_IDLE = timedelta(days=30)


def _hold(code: str) -> dict[str, Any]:
    return {
        "eligible": False,
        "reason_code": code,
        "historical_paid_merge_proof": False,
        "new_work_only": True,
        "payment_to_our_claimant_verified": False,
    }


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 60:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _url(value: Any, host: str | None = None) -> tuple[str, str] | None:
    if not isinstance(value, str) or not 12 <= len(value) <= 2048:
        return None
    if any(ch.isspace() for ch in value) or "\\" in value or "%" in value:
        return None
    try:
        p = urlsplit(value)
        port = p.port
    except ValueError:
        return None
    if (p.scheme != "https" or not p.hostname or p.username or p.password
            or port is not None or p.netloc != p.hostname or p.query or p.fragment
            or not p.path.startswith("/")):
        return None
    if host is not None and p.hostname != host:
        return None
    return p.hostname, p.path


def evaluate_payer_history(
    snapshot: dict[str, Any],
    owner_registry: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fail closed unless a fresh, same-repository curated record proves paid history.

    Passing means prior payer payment evidence is *recorded*, not that the
    present issue is awarded, funded, payable to us, or guaranteed to pay.
    """
    if not isinstance(snapshot, dict):
        return _hold("CANONICAL_REPOSITORY_IDENTITY_MISSING")
    repo = snapshot.get("repository_full_name")
    audit = snapshot.get("canonical_audit")
    audited_repo = audit.get("repository_full_name") if isinstance(audit, dict) else None
    if (not isinstance(repo, str) or _REPO.fullmatch(repo) is None
            or (audited_repo is not None and
                (not isinstance(audited_repo, str) or audited_repo.casefold() != repo.casefold()))):
        return _hold("CANONICAL_REPOSITORY_IDENTITY_MISSING")
    if (not isinstance(owner_registry, dict)
            or type(owner_registry.get("schema_version")) is not int
            or owner_registry["schema_version"] != 1
            or not isinstance(owner_registry.get("repositories"), dict)):
        return _hold("PAYER_REGISTRY_MISSING_OR_INVALID")

    # The owner's current exact-payer policy is 30 days, not the superseded
    # 90-day historical sponsor-discovery heuristic.
    if type(owner_registry.get("activity_max_age_days")) is not int or owner_registry.get("activity_max_age_days") != 30:
        return _hold("PAYER_ACTIVITY_POLICY_MISMATCH")
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        return _hold("PAYER_GATE_CLOCK_INVALID")
    now = now.astimezone(timezone.utc)
    observed = _utc(owner_registry.get("observed_at"))
    if observed is None or observed > now or now - observed > MAX_SNAPSHOT_AGE:
        return _hold("PAYER_REGISTRY_STALE")

    matches = [(key, value) for key, value in owner_registry["repositories"].items()
               if isinstance(key, str) and key.casefold() == repo.casefold()]
    if len(matches) != 1:
        return _hold("SAME_PAYER_PAID_MERGE_UNVERIFIED")
    entry = matches[0][1]
    if not isinstance(entry, dict) or entry.get("status") != "QUALIFIED_ACTIVE_PAID":
        return _hold("SAME_PAYER_PAID_MERGE_UNVERIFIED")
    activity = entry.get("maintainer_activity")
    last = _utc(activity.get("event_at")) if isinstance(activity, dict) else None
    if last is None or last > now or now - last > MAX_MAINTAINER_IDLE:
        return _hold("PAID_PAYER_MAINTAINER_INACTIVE")
    if entry.get("unresolved_overdue_accepted_bounties"):
        return _hold("PAYER_UNRESOLVED_OVERDUE_ACCEPTED_BOUNTY")
    history = entry.get("paid_merge_history")
    if not isinstance(history, list) or not history:
        return _hold("SAME_PAYER_PAID_MERGE_UNVERIFIED")
    for event in history:
        if not isinstance(event, dict):
            continue
        pr = _url(event.get("merged_pr_url"), "github.com")
        receipt = _url(event.get("payment_evidence_url"))
        payer = event.get("payer")
        recipient = event.get("recipient")
        if (pr is None or _PR.fullmatch(pr[1]) is None
                or receipt is None or receipt[0] not in _RECEIPT_HOSTS
                or not isinstance(payer, str) or not payer.strip()
                or not isinstance(recipient, str) or not recipient.strip()):
            continue
        return {
            # Historical payment can qualify a SPONSOR for discovery only.
            # The separate exact repository/platform/issue/claimant/amount/payout
            # canonical checker is not represented in this mirror; NEVER turn
            # history into a new-work permit here.
            "eligible": False,
            "reason_code": "EXACT_TASK_PREFLIGHT_REQUIRED",
            "historical_paid_merge_proof": True,
            "registry_repository": repo.casefold(),
            "historical_merged_pr_url": event["merged_pr_url"],
            "historical_payment_evidence_url": event["payment_evidence_url"],
            "new_work_only": True,
            "payment_to_our_claimant_verified": False,
        }
    return _hold("SAME_PAYER_PAID_MERGE_UNVERIFIED")
