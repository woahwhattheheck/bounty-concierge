# SPDX-License-Identifier: MIT
"""Fail-closed qualification gate for paid-work dispatch.

This module evaluates a normalized bounty snapshot *before* implementation is
dispatched. It complements ``bounty_audit``: the audit establishes canonical
GitHub competition/staleness signals, while this gate combines those signals
with live reward metadata and contribution-term boundaries.

The returned result intentionally never includes source body/comment text.
Raw comment bodies are accepted only with explicit GitHub author-association
metadata; only OWNER/MEMBER/COLLABORATOR comments are authoritative terms.
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
_KEYWORD_REWARD_RE = re.compile(
    r"(?i)\b(?:bounty|reward)(?:\s+(?:amount|payout))?"
    r"\s*(?::|=|-|\bis\b|\bof\b)?\s*"
    r"\$([0-9][0-9,]*(?:\.[0-9]{1,2})?)\b"
)
_AMOUNT_BEFORE_REWARD_RE = re.compile(
    r"(?i)(?<![\w.])\$([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
    r"\s+(?:bounty|reward)\b"
)
_LABEL_REWARD_RE = re.compile(
    r"(?<![\w.])\$([0-9][0-9,]*(?:\.[0-9]{1,2})?)\b"
)

_RTC_NUMBER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
_RTC_TOKEN_RE = re.compile(
    rf"(?i)(?<![\w.,])({_RTC_NUMBER})\s*RTC\b"
)
_RTC_RANGE_RE = re.compile(
    rf"(?i)(?<![\w.,])({_RTC_NUMBER})\s*[-–—]\s*({_RTC_NUMBER})\s*RTC\b"
)
_RTC_KEYWORD_REWARD_RE = re.compile(
    rf"(?i)\b(?:bounty|reward)(?:\s+(?:amount|payout))?"
    rf"\s*(?::|=|-|\bis\b|\bof\b)?\s*\**\s*({_RTC_NUMBER})\s*RTC\b"
)
_RTC_AMOUNT_BEFORE_REWARD_RE = re.compile(
    rf"(?i)(?<![\w.,])({_RTC_NUMBER})\s*RTC\s+(?:bounty|reward)\b"
)
_RTC_SPEC_RE = re.compile(
    rf"(?im)^\s*reward_rtc\s*:\s*({_RTC_NUMBER})\s*(?:#.*)?$"
)

_REWARDED_LABEL_RE = re.compile(r"\brewarded\b", re.IGNORECASE)
_BOUNTY_WORD_RE = re.compile(r"\b(?:bounty|reward)\b", re.IGNORECASE)
_MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

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


def _advertised_rewards(text: str) -> set[Decimal]:
    """Extract high-confidence sponsor-advertised USD amounts from one field."""
    values = {_amount(match) for match in _BODY_BOUNTY_RE.findall(text)}
    values.update(_amount(match) for match in _KEYWORD_REWARD_RE.findall(text))
    values.update(_amount(match) for match in _AMOUNT_BEFORE_REWARD_RE.findall(text))
    return values


def _has_bounty_label(labels: list[str]) -> bool:
    return any(label.strip().casefold() == "bounty" for label in labels)


def _title_rtc_rewards(title: str, labels: list[str]) -> set[Decimal]:
    """Return the sponsor-authoritative RTC figure encoded in an issue title.

    Elyan Labs currently declares the title figure authoritative when older
    issue bodies retain pre-adjustment amounts.  A range is deliberately kept
    as two values so dispatch fails closed as ambiguous; otherwise the first RTC
    figure is the title reward, matching the existing bounty-index ordering.
    """
    if not title or not (_BOUNTY_WORD_RE.search(title) or _has_bounty_label(labels)):
        return set()

    range_match = _RTC_RANGE_RE.search(title)
    if range_match:
        return {_amount(range_match.group(1)), _amount(range_match.group(2))}

    match = _RTC_TOKEN_RE.search(title)
    if match:
        return {_amount(match.group(1))}
    return set()


def _body_rtc_rewards(body: str) -> set[Decimal]:
    """Extract high-confidence RTC reward declarations from an issue body."""
    values = {_amount(match) for match in _RTC_KEYWORD_REWARD_RE.findall(body)}
    values.update(_amount(match) for match in _RTC_AMOUNT_BEFORE_REWARD_RE.findall(body))
    values.update(_amount(match) for match in _RTC_SPEC_RE.findall(body))
    return values


def _label_rtc_rewards(labels: list[str]) -> set[Decimal]:
    """Extract explicit RTC amounts from live labels."""
    return {
        _amount(match)
        for label in labels
        for match in _RTC_TOKEN_RE.findall(label)
    }


def _title_value(snapshot: dict[str, Any]) -> str:
    value = snapshot.get("title", "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise QualificationInputError("title must be a string")
    return value


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


def _text_values(snapshot: dict[str, Any]) -> tuple[list[str], int, int]:
    """Return trusted term text plus safe comment-authority counters.

    ``body``, ``contribution_terms``, and ``requirements`` are normalized caller
    fields. Raw ``comments`` sit on a different trust boundary: every comment
    must carry GitHub ``author_association`` metadata and only maintainer-authority
    comments may influence private-context rejection. External comments are
    ignored rather than allowed to suppress otherwise valid paid work.
    """
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

    trusted_comment_count = 0
    ignored_untrusted_comment_count = 0
    comments = snapshot.get("comments")
    if comments is not None:
        if not isinstance(comments, list):
            raise QualificationInputError("comments must be a list")
        for comment in comments:
            if not isinstance(comment, dict):
                raise QualificationInputError(
                    "each comment must be an object with string body and author_association"
                )
            comment_body = comment.get("body")
            association = comment.get("author_association")
            if not isinstance(comment_body, str):
                raise QualificationInputError("each comment body must be a string")
            if not isinstance(association, str) or not association.strip():
                raise QualificationInputError(
                    "each comment author_association must be a non-empty string"
                )
            if association.strip().upper() in _MAINTAINER_ASSOCIATIONS:
                texts.append(comment_body)
                trusted_comment_count += 1
            else:
                ignored_untrusted_comment_count += 1

    return texts, trusted_comment_count, ignored_untrusted_comment_count


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
    Raw comment text can affect the decision only when the snapshot supplies an
    OWNER/MEMBER/COLLABORATOR ``author_association`` for that comment.

    USD and RTC are independent native reward currencies here.  RTC is never
    converted to USD.  For RTC, a title amount is sponsor-authoritative and a
    body amount is only a fallback when the title carries no RTC figure.
    """
    if not isinstance(snapshot, dict):
        raise QualificationInputError("snapshot must be an object")
    if (
        isinstance(saturation_threshold, bool)
        or not isinstance(saturation_threshold, int)
        or saturation_threshold <= 0
    ):
        raise QualificationInputError("saturation_threshold must be a positive integer")

    title = _title_value(snapshot)
    labels = _label_names(snapshot)
    texts, trusted_comment_count, ignored_untrusted_comment_count = _text_values(snapshot)
    audit, audit_complete = _canonical_audit(snapshot)

    body_rewards = _advertised_rewards(texts[0])
    title_rewards = _advertised_rewards(title)
    advertised_rewards = body_rewards | title_rewards
    label_rewards = {
        _amount(match) for label in labels for match in _LABEL_REWARD_RE.findall(label)
    }

    title_rtc_rewards = _title_rtc_rewards(title, labels)
    body_rtc_rewards = _body_rtc_rewards(texts[0])
    label_rtc_rewards = _label_rtc_rewards(labels)
    if title_rtc_rewards:
        advertised_rtc_rewards = title_rtc_rewards
        rtc_reward_source = "title"
    elif body_rtc_rewards:
        advertised_rtc_rewards = body_rtc_rewards
        rtc_reward_source = "body"
    elif label_rtc_rewards:
        advertised_rtc_rewards = set()
        rtc_reward_source = "label"
    else:
        advertised_rtc_rewards = set()
        rtc_reward_source = None

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
    if (
        not advertised_rewards
        and not label_rewards
        and not advertised_rtc_rewards
        and not label_rtc_rewards
    ):
        add(
            "REWARD_NOT_ADVERTISED",
            "HOLD",
            "No explicit paid reward amount is advertised in canonical issue metadata.",
        )
    if len(advertised_rewards) > 1:
        add(
            "AMBIGUOUS_ADVERTISED_REWARD",
            "HOLD",
            "Issue title/body contains more than one distinct advertised USD reward amount.",
        )
    if len(advertised_rtc_rewards) > 1:
        add(
            "AMBIGUOUS_ADVERTISED_REWARD",
            "HOLD",
            "Authoritative RTC reward source contains more than one distinct amount.",
        )
    if len(label_rewards) > 1 or len(label_rtc_rewards) > 1:
        add(
            "AMBIGUOUS_LIVE_REWARD",
            "HOLD",
            "Live labels contain more than one distinct reward amount.",
        )
    if advertised_rewards and label_rewards and advertised_rewards != label_rewards:
        add(
            "REWARD_MISMATCH",
            "HOLD",
            "Advertised USD reward and live USD label reward disagree.",
        )
    if (
        advertised_rtc_rewards
        and label_rtc_rewards
        and advertised_rtc_rewards != label_rtc_rewards
    ):
        add(
            "REWARD_MISMATCH",
            "HOLD",
            "Authoritative RTC reward and live RTC label reward disagree.",
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
            "title_reward_usd": _amount_strings(title_rewards),
            "advertised_reward_usd": _amount_strings(advertised_rewards),
            "live_label_reward_usd": _amount_strings(label_rewards),
            "body_reward_rtc": _amount_strings(body_rtc_rewards),
            "title_reward_rtc": _amount_strings(title_rtc_rewards),
            "advertised_reward_rtc": _amount_strings(advertised_rtc_rewards),
            "live_label_reward_rtc": _amount_strings(label_rtc_rewards),
            "rtc_reward_source": rtc_reward_source,
            "already_rewarded": already_rewarded,
            "attempt_count": attempt_count,
            "open_pr_count": open_pr_count,
            "private_context_signal_types": private_signal_types,
            "trusted_comment_count": trusted_comment_count,
            "ignored_untrusted_comment_count": ignored_untrusted_comment_count,
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
            "Fail closed on unpaid/unadvertised, contradictory, saturated, stale, "
            "or private-context paid-work listings."
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
