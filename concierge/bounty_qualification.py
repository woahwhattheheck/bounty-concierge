# SPDX-License-Identifier: MIT
"""Fail-closed qualification gate for paid-work dispatch.

This module evaluates a normalized bounty snapshot *before* implementation is
dispatched. It complements ``bounty_audit``: the audit establishes canonical
GitHub competition/staleness signals, while this gate combines those signals
with live reward metadata and contribution-term boundaries.

The returned result intentionally never includes source body/comment text.
"""

from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


_BODY_BOUNTY_RE = re.compile(
    r"(?im)(?:^|\s)/bounty\s+\$([0-9][0-9,]*(?:\.[0-9]{1,2})?)\b"
)
_LABEL_REWARD_RE = re.compile(
    r"(?<![\w.])\$([0-9][0-9,]*(?:\.[0-9]{1,2})?)\b"
)
_REWARDED_LABEL_RE = re.compile(r"\brewarded\b", re.IGNORECASE)

_PRIVATE_CONTEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("system_prompt", re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE)),
    ("developer_prompt", re.compile(r"\bdeveloper\s+prompt\b", re.IGNORECASE)),
    (
        "private_context",
        re.compile(r"\b(?:private|hidden|session|runtime|model)\s+context\b", re.IGNORECASE),
    ),
    (
        "conversation_history",
        re.compile(r"\b(?:full\s+)?(?:conversation|chat)\s+(?:history|transcript)\b", re.IGNORECASE),
    ),
    ("hidden_instructions", re.compile(r"\bhidden\s+instructions?\b", re.IGNORECASE)),
    (
        "full_runtime_configuration",
        re.compile(r"\bfull\s+runtime\s+configuration\b", re.IGNORECASE),
    ),
)


class QualificationInputError(ValueError):
    """Raised when a dispatch snapshot is structurally unreliable."""


def _amount(value: str) -> Decimal:
    try:
        amount = Decimal(value.replace(",", ""))
    except (InvalidOperation, AttributeError) as exc:
        raise QualificationInputError(f"invalid reward amount: {value!r}") from exc
    if not amount.is_finite() or amount < 0:
        raise QualificationInputError(f"invalid reward amount: {value!r}")
    return amount


def _amount_strings(values: set[Decimal]) -> list[str]:
    result = []
    for value in sorted(values):
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        result.append(text)
    return result


def _label_names(snapshot: dict[str, Any]) -> list[str]:
    raw = snapshot.get("labels", [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise QualificationInputError("labels must be a list")
    names: list[str] = []
    for item in raw:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
        else:
            raise QualificationInputError(
                "each label must be a string or an object with a string name"
            )
    return names


def _text_values(snapshot: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    body = snapshot.get("body", "")
    if body is None:
        body = ""
    if not isinstance(body, str):
        raise QualificationInputError("body must be a string")
    texts.append(body)

    for key in ("contribution_terms", "requirements"):
        value = snapshot.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            texts.extend(value)
        else:
            raise QualificationInputError(f"{key} must be a string or list of strings")

    comments = snapshot.get("comments")
    if comments is not None:
        if not isinstance(comments, list):
            raise QualificationInputError("comments must be a list")
        for comment in comments:
            if isinstance(comment, str):
                texts.append(comment)
            elif isinstance(comment, dict) and isinstance(comment.get("body"), str):
                texts.append(comment["body"])
            else:
                raise QualificationInputError(
                    "each comment must be a string or an object with a string body"
                )
    return texts


def _nonnegative_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise QualificationInputError(f"{name} must be a non-negative integer")
    return value


def _canonical_audit(snapshot: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if "canonical_audit" not in snapshot or snapshot["canonical_audit"] is None:
        return {}, False
    value = snapshot["canonical_audit"]
    if not isinstance(value, dict):
        raise QualificationInputError("canonical_audit must be an object")
    required = {"issue_state", "open_pr_count", "stale_listing_signal", "search_truncated"}
    return value, required.issubset(value)


def qualify_dispatch(
    snapshot: dict[str, Any], *, saturation_threshold: int = 4
) -> dict[str, Any]:
    """Return a safe paid-work dispatch decision for one normalized snapshot.

    Decision precedence is ``REJECT`` > ``HOLD`` > ``ACTIONABLE``.
    The result is safe to log: source issue/comment text is never copied into it.
    """
    if not isinstance(snapshot, dict):
        raise QualificationInputError("snapshot must be an object")
    if (
        isinstance(saturation_threshold, bool)
        or not isinstance(saturation_threshold, int)
        or saturation_threshold <= 0
    ):
        raise QualificationInputError("saturation_threshold must be a positive integer")

    labels = _label_names(snapshot)
    texts = _text_values(snapshot)
    audit, audit_complete = _canonical_audit(snapshot)

    body_rewards = {_amount(match) for match in _BODY_BOUNTY_RE.findall(texts[0])}
    label_rewards = {
        _amount(match) for label in labels for match in _LABEL_REWARD_RE.findall(label)
    }
    private_signal_types = sorted(
        {
            name
            for text in texts
            for name, pattern in _PRIVATE_CONTEXT_PATTERNS
            if pattern.search(text)
        }
    )
    already_rewarded = any(_REWARDED_LABEL_RE.search(label) for label in labels)

    attempt_count = _nonnegative_int(snapshot.get("attempt_count"), "attempt_count")
    open_pr_count = _nonnegative_int(
        audit.get("open_pr_count"), "canonical_audit.open_pr_count"
    )
    issue_state = audit.get("issue_state")
    if issue_state is not None and not isinstance(issue_state, str):
        raise QualificationInputError("canonical_audit.issue_state must be a string")
    stale_listing = audit.get("stale_listing_signal", False)
    search_truncated = audit.get("search_truncated", False)
    if not isinstance(stale_listing, bool):
        raise QualificationInputError(
            "canonical_audit.stale_listing_signal must be boolean"
        )
    if not isinstance(search_truncated, bool):
        raise QualificationInputError("canonical_audit.search_truncated must be boolean")

    reasons: list[dict[str, str]] = []

    def add(code: str, severity: str, message: str) -> None:
        reasons.append({"code": code, "severity": severity, "message": message})

    if private_signal_types:
        add(
            "PRIVATE_CONTEXT_REQUIRED",
            "REJECT",
            "Contribution terms request private execution/session context.",
        )
    if already_rewarded:
        add(
            "ALREADY_REWARDED",
            "REJECT",
            "Live labels indicate that the bounty has already been rewarded.",
        )
    if audit_complete and issue_state and issue_state.lower() != "open":
        add(
            "ISSUE_NOT_OPEN",
            "REJECT",
            "Canonical repository state says the issue is not open.",
        )

    if not audit_complete:
        add(
            "CANONICAL_AUDIT_MISSING",
            "HOLD",
            "Canonical issue/competition audit is missing or incomplete.",
        )
    if len(body_rewards) > 1:
        add(
            "AMBIGUOUS_ADVERTISED_REWARD",
            "HOLD",
            "Issue body contains more than one distinct /bounty amount.",
        )
    if len(label_rewards) > 1:
        add(
            "AMBIGUOUS_LIVE_REWARD",
            "HOLD",
            "Live labels contain more than one distinct reward amount.",
        )
    if body_rewards and label_rewards and body_rewards != label_rewards:
        add(
            "REWARD_MISMATCH",
            "HOLD",
            "Advertised body reward and live label reward disagree.",
        )

    saturated_by_attempts = (
        attempt_count is not None and attempt_count >= saturation_threshold
    )
    saturated_by_prs = (
        open_pr_count is not None and open_pr_count >= saturation_threshold
    )
    if saturated_by_attempts or saturated_by_prs:
        add(
            "SATURATED_COMPETITION",
            "HOLD",
            "Competition meets or exceeds the configured dispatch threshold.",
        )
    if stale_listing:
        add(
            "STALE_LISTING_SIGNAL",
            "HOLD",
            "Canonical repository state indicates that the open listing may be stale.",
        )
    if search_truncated:
        add(
            "CANONICAL_AUDIT_INCOMPLETE",
            "HOLD",
            "Canonical audit search was truncated; competition state is incomplete.",
        )

    if any(reason["severity"] == "REJECT" for reason in reasons):
        disposition = "REJECT"
    elif reasons:
        disposition = "HOLD"
    else:
        disposition = "ACTIONABLE"

    return {
        "disposition": disposition,
        "dispatch": disposition == "ACTIONABLE",
        "reason_codes": [reason["code"] for reason in reasons],
        "reasons": reasons,
        "signals": {
            "body_reward_usd": _amount_strings(body_rewards),
            "live_label_reward_usd": _amount_strings(label_rewards),
            "already_rewarded": already_rewarded,
            "attempt_count": attempt_count,
            "open_pr_count": open_pr_count,
            "private_context_signal_types": private_signal_types,
            "stale_listing_signal": stale_listing,
            "search_truncated": search_truncated,
            "canonical_audit_complete": audit_complete,
            "issue_state": issue_state,
            "saturation_threshold": saturation_threshold,
        },
    }


def format_summary(result: dict[str, Any]) -> str:
    """Format a compact operator-safe summary without source contribution text."""
    codes = ",".join(result.get("reason_codes", [])) or "none"
    return (
        f"disposition={result['disposition']} "
        f"dispatch={str(result['dispatch']).lower()} reasons={codes}"
    )


def _load_snapshot(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        payload = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, dict):
        raise QualificationInputError("snapshot JSON must contain an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    """Qualify a normalized bounty snapshot from a JSON file or stdin."""
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bounty_qualification",
        description=(
            "Fail closed on contradictory, saturated, stale, or private-context "
            "paid-work listings."
        ),
    )
    parser.add_argument("snapshot", help="JSON snapshot path, or - for stdin")
    parser.add_argument(
        "--saturation-threshold",
        type=int,
        default=4,
        help="Hold dispatch at this many attempts/open PRs (default: 4)",
    )
    parser.add_argument("--json", action="store_true", help="Emit full result JSON")
    args = parser.parse_args(argv)

    try:
        snapshot = _load_snapshot(args.snapshot)
        result = qualify_dispatch(
            snapshot, saturation_threshold=args.saturation_threshold
        )
    except (OSError, json.JSONDecodeError, QualificationInputError) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_summary(result))

    if result["disposition"] == "ACTIONABLE":
        return 0
    if result["disposition"] == "HOLD":
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
