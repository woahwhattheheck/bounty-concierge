# SPDX-License-Identifier: MIT
"""Live, source-bound cash admission for GitHub bounty issues.

The caller supplies repository/issue identity and an optional source-bound
submission target, never reward amount, authority, or historical evaluation
time. Canonical issue state comes from bounty_preflight and is re-read before
routing.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import requests

from concierge import bounty_preflight as bp
from concierge.submission_packet import validate_submission_target


_RECEIPT_SCHEMA = "bounty-live-cash-admission-receipt/v1"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_USD_TOKEN_RE = re.compile(r"\$([0-9][0-9,]*(?:\.[0-9]{1,2})?)\b")
_MAX_USD_AMOUNT = Decimal("1000000000000")
_FIXED_USD_RE = re.compile(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{1,8})?\Z")
_REWARD_WORD_RE = re.compile(r"(?i)\b(?:bounty|reward|prize)\b|/(?:bounty)\b")
_REWARD_CONTEXT_RE = re.compile(
    r"(?i)\b(?:bounty|reward|prize|payment|payout|amount|compensation|milestones?)\b"
)
_NONFIXED_WORD_RE = re.compile(
    r"(?i)\b(?:up\s+to|at\s+most|max(?:imum)?|ceiling|range|between|"
    r"variable|depend(?:s|ing)?\s+on|prize\s+pool|reward\s+pool|bounty\s+pool)\b"
)
_MILESTONE_TOTAL_RE = re.compile(
    r"(?i)(?:\bmilestones?\b.{0,40}\btotal\b|\btotal\b.{0,40}\bmilestones?\b)"
)
_AUTHORITY = {
    "advisory_only": True,
    "claim_authority": False,
    "implementation_authority": False,
    "submission_authority": False,
    "outbound_contact_authority": False,
    "payment_or_wallet_authority": False,
}


class LiveCashAdmissionError(RuntimeError):
    """Raised when live canonical evidence cannot be evaluated safely."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _repo(value: str) -> str:
    if not isinstance(value, str) or not _REPO_RE.fullmatch(value):
        raise ValueError("repo must be owner/name using GitHub-safe characters")
    owner, name = value.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise ValueError("repo must not contain dot path segments")
    return value


def _issue_number(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("number must be a positive issue number")
    return value


def _decimal(value: Any, field: str) -> Decimal:
    # Live GitHub titles/labels are untrusted input. Never accept exponents or
    # unconstrained digits: format(Decimal("1e100000"), "f") expands >100 KiB.
    # Preserve ordinary fixed USD tokens, including properly grouped thousands.
    if not isinstance(value, str) or not value or value != value.strip():
        raise LiveCashAdmissionError(f"{field} must be an exact decimal string")
    if len(value) > 48 or _FIXED_USD_RE.fullmatch(value) is None:
        raise LiveCashAdmissionError(f"{field} must be bounded fixed-point USD")
    try:
        amount = Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise LiveCashAdmissionError(f"{field} is not a valid decimal") from exc
    if not amount.is_finite() or amount < 0 or amount > _MAX_USD_AMOUNT:
        raise LiveCashAdmissionError(f"{field} is outside the supported USD range")
    return amount


def _format_decimal(value: Decimal) -> str:
    # Keep this final output boundary independent of the input parser.
    if (
        not isinstance(value, Decimal) or not value.is_finite() or value < 0
        or value > _MAX_USD_AMOUNT or value.as_tuple().exponent < -8
    ):
        raise LiveCashAdmissionError("fixed USD amount exceeds formatting bounds")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _source_text(issue: dict[str, Any]) -> tuple[str, str, list[str]]:
    title = issue.get("title") or ""
    body = issue.get("body") or ""
    if not isinstance(title, str) or not isinstance(body, str):
        raise LiveCashAdmissionError("canonical issue title/body was malformed")
    try:
        labels = bp._label_names(issue)
    except (TypeError, ValueError, bp.BountyPreflightError) as exc:
        raise LiveCashAdmissionError("canonical issue labels were malformed") from exc
    return title, body, labels


def _nonfixed_usd_semantics(
    issue: dict[str, Any], selected_amount: Decimal | None
) -> bool:
    """Identify ceilings/ranges/pools and never treat them as fixed cash."""
    title, body, labels = _source_text(issue)
    for text in (title, body, *labels):
        lines = text.splitlines() or [text]
        for index, line in enumerate(lines):
            raw_amounts = _USD_TOKEN_RE.findall(line)
            if not raw_amounts:
                continue
            amounts = {
                _decimal(value, "canonical source USD token")
                for value in raw_amounts
            }
            selected_present = (
                selected_amount is not None and selected_amount in amounts
            )
            qualified = (
                _NONFIXED_WORD_RE.search(line) is not None
                or _MILESTONE_TOTAL_RE.search(line) is not None
            )
            if selected_present and qualified:
                return True
            if selected_present:
                for neighbor_index in (index - 1, index + 1):
                    if neighbor_index < 0 or neighbor_index >= len(lines):
                        continue
                    neighbor = lines[neighbor_index]
                    neighbor_qualified = (
                        _NONFIXED_WORD_RE.search(neighbor) is not None
                        or _MILESTONE_TOTAL_RE.search(neighbor) is not None
                    )
                    if neighbor_qualified and _REWARD_CONTEXT_RE.search(neighbor):
                        return True
            if _REWARD_WORD_RE.search(line) and len(amounts) > 1:
                return True
    return False


def _read_issue_generation(
    repo: str, number: int, token: str | None, *, session: Any
) -> tuple[dict[str, Any], tuple[Any, ...]]:
    effective_token = token or bp.GITHUB_TOKEN
    url = f"https://api.github.com/repos/{repo}/issues/{number}"
    try:
        payload = bp._object_payload(
            bp._get_json(session, url, headers=bp._headers(effective_token)),
            f"issue {repo}#{number}",
        )
        if "pull_request" in payload:
            raise ValueError(f"{repo}#{number} is a pull request, not an issue")
        marker = bp._issue_generation_marker(payload)
    except bp.BountyPreflightError as exc:
        raise LiveCashAdmissionError(str(exc)) from exc
    return payload, marker



def _read_repository_snapshot(
    repo: str, token: str | None, *, session: Any
) -> dict[str, Any]:
    """Read canonical sponsor repository state, not a marketplace listing.

    Only run this additional GitHub request after an otherwise dispatchable
    issue passes the issue-generation/preflight checks. A malformed, relocated
    or inaccessible sponsor repository fails closed instead of leasing work.
    """
    effective_token = token or bp.GITHUB_TOKEN
    try:
        payload = bp._object_payload(
            bp._get_json(
                session, f"https://api.github.com/repos/{repo}",
                headers=bp._headers(effective_token),
            ),
            f"repository {repo}",
        )
    except bp.BountyPreflightError as exc:
        raise LiveCashAdmissionError(
            f"canonical sponsor repository check failed: {exc}"
        ) from exc
    full_name = payload.get("full_name")
    archived, fork = payload.get("archived"), payload.get("fork")
    if (
        not isinstance(full_name, str)
        or _REPO_RE.fullmatch(full_name) is None
        or type(archived) is not bool
        or type(fork) is not bool
    ):
        raise LiveCashAdmissionError("canonical repository snapshot was malformed")
    snapshot = {"full_name": full_name, "archived": archived, "fork": fork}
    if full_name.casefold() != repo.casefold():
        snapshot["relocated_from"] = repo
    return snapshot



def _usd_amount_from_preflight(preflight: dict[str, Any]) -> Decimal | None:
    qualification = preflight.get("qualification")
    if not isinstance(qualification, dict):
        raise LiveCashAdmissionError("preflight qualification was malformed")
    signals = qualification.get("signals")
    if not isinstance(signals, dict):
        raise LiveCashAdmissionError("preflight qualification signals were malformed")
    raw_values: list[Any] = []
    for field in ("advertised_reward_usd", "live_label_reward_usd"):
        values = signals.get(field, [])
        if not isinstance(values, list):
            raise LiveCashAdmissionError(f"preflight signal {field} was malformed")
        raw_values.extend(values)
    values = {_decimal(value, "preflight USD reward") for value in raw_values}
    if not values:
        return None
    if len(values) != 1:
        raise LiveCashAdmissionError("preflight emitted conflicting USD reward values")
    return next(iter(values))


def _has_native_non_usd_reward(preflight: dict[str, Any]) -> bool:
    qualification = preflight.get("qualification")
    signals = qualification.get("signals") if isinstance(qualification, dict) else None
    if not isinstance(signals, dict):
        raise LiveCashAdmissionError("preflight qualification signals were malformed")
    for field in ("advertised_reward_rtc", "live_label_reward_rtc"):
        values = signals.get(field, [])
        if not isinstance(values, list):
            raise LiveCashAdmissionError(f"preflight signal {field} was malformed")
        if values:
            return True
    return False


def _safe_preflight_projection(preflight: dict[str, Any]) -> dict[str, Any]:
    qualification = preflight.get("qualification")
    audit = preflight.get("canonical_audit")
    if not isinstance(qualification, dict) or not isinstance(audit, dict):
        raise LiveCashAdmissionError("preflight result was malformed")
    signals = qualification.get("signals")
    if not isinstance(signals, dict):
        raise LiveCashAdmissionError("preflight qualification signals were malformed")
    reason_codes = qualification.get("reason_codes", [])
    if not isinstance(reason_codes, list) or not all(
        isinstance(value, str) for value in reason_codes
    ):
        raise LiveCashAdmissionError("preflight reason_codes were malformed")
    return {
        "disposition": qualification.get("disposition"),
        "dispatch": qualification.get("dispatch"),
        "reason_codes": list(reason_codes),
        "signals": {
            key: signals.get(key)
            for key in (
                "advertised_reward_usd",
                "live_label_reward_usd",
                "advertised_reward_rtc",
                "live_label_reward_rtc",
                "already_rewarded",
                "attempt_count",
                "open_pr_count",
                "stale_listing_signal",
                "search_truncated",
                "canonical_audit_complete",
                "issue_state",
                "canonical_generation_stable",
                "canonical_audit_stable",
            )
        },
        "audit": {
            key: audit.get(key)
            for key in (
                "issue_state",
                "open_pr_count",
                "stale_listing_signal",
                "search_truncated",
            )
        },
    }


def _build_api():
    active_floor = Decimal("15")
    pile_floor = Decimal("10")
    main_route = "main_bounty_queue"
    pile_route = "bounty_pile_10_49"

    def evaluate_live_cash_admission(
        repo: str,
        number: int,
        token: str | None = None,
        *,
        session: Any = requests,
        max_pages: int = 10,
        saturation_threshold: int = 4,
        operator_login: str | None = None,
        submission_target: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Evaluate one GitHub issue using only live canonical source evidence.

        Default reads share one owned connection pool through nested preflight.
        A caller-supplied session remains owned and managed by that caller.
        """
        repo_norm = _repo(repo)
        issue_num = _issue_number(number)
        if submission_target is not None:
            submission_target = validate_submission_target(
                submission_target, f"https://github.com/{repo_norm}/issues/{issue_num}"
            )
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
            raise ValueError("max_pages must be a positive integer")
        if (
            isinstance(saturation_threshold, bool)
            or not isinstance(saturation_threshold, int)
            or saturation_threshold <= 0
        ):
            raise ValueError("saturation_threshold must be a positive integer")

        if session is requests:
            with requests.Session() as owned_session:
                return evaluate_live_cash_admission(
                    repo_norm,
                    issue_num,
                    token,
                    session=owned_session,
                    max_pages=max_pages,
                    saturation_threshold=saturation_threshold,
                    operator_login=operator_login,
                    submission_target=submission_target,
                )

        issue_before, marker_before = _read_issue_generation(
            repo_norm, issue_num, token, session=session
        )
        try:
            target_kwargs = (
                {"submission_target": submission_target}
                if submission_target is not None else {}
            )
            preflight = bp.preflight_bounty(
                repo_norm,
                issue_num,
                token,
                session=session,
                max_pages=max_pages,
                saturation_threshold=saturation_threshold,
                operator_login=operator_login,
                **target_kwargs,
            )
        except (bp.BountyPreflightError, ValueError, bp.QualificationInputError) as exc:
            raise LiveCashAdmissionError(f"canonical preflight failed: {exc}") from exc
        issue_after, marker_after = _read_issue_generation(
            repo_norm, issue_num, token, session=session
        )

        qualification = preflight.get("qualification")
        if not isinstance(qualification, dict):
            raise LiveCashAdmissionError("preflight qualification was malformed")
        dispatch = qualification.get("dispatch")
        if not isinstance(dispatch, bool):
            raise LiveCashAdmissionError("preflight dispatch flag was malformed")
        preflight_disposition = qualification.get("disposition")
        if not isinstance(preflight_disposition, str):
            raise LiveCashAdmissionError("preflight disposition was malformed")

        generation_stable = marker_before == marker_after
        amount = _usd_amount_from_preflight(preflight)
        nonfixed = _nonfixed_usd_semantics(issue_after, amount)
        native_non_usd = _has_native_non_usd_reward(preflight)

        # Save rate budget: check the repository only for an otherwise
        # actionable paid row; blocked/closed/unfunded issues need no extra GET.
        repository = (
            _read_repository_snapshot(repo_norm, token, session=session)
            if generation_stable and dispatch and amount is not None
            and not nonfixed and not native_non_usd and amount >= pile_floor
            else None
        )

        reasons: list[str] = []
        route: str | None = None
        if not generation_stable:
            disposition = "HOLD_SOURCE_GENERATION_CHANGED"
            reasons.append("CANONICAL_ISSUE_GENERATION_CHANGED")
        elif not dispatch:
            disposition = (
                "REJECT_CANONICAL_PREFLIGHT"
                if preflight_disposition == "REJECT"
                else "HOLD_CANONICAL_PREFLIGHT"
            )
            raw_reasons = qualification.get("reason_codes", [])
            if not isinstance(raw_reasons, list) or not all(
                isinstance(value, str) for value in raw_reasons
            ):
                raise LiveCashAdmissionError("preflight reason_codes were malformed")
            reasons.extend(raw_reasons)
        elif repository is not None and repository["archived"]:
            disposition = "REJECT_CANONICAL_REPOSITORY_ARCHIVED"
            reasons.append("CANONICAL_REPOSITORY_ARCHIVED")
        elif repository is not None and "relocated_from" in repository:
            disposition = "HOLD_CANONICAL_REPOSITORY_RELOCATED"
            reasons.append("CANONICAL_REPOSITORY_AND_ISSUE_RECHECK_REQUIRED")
        elif repository is not None and repository["fork"]:
            # A sponsor fork may still be payable, but needs explicit review.
            disposition = "HOLD_CANONICAL_REPOSITORY_FORK"
            reasons.append("CANONICAL_REPOSITORY_FORK_REVIEW_REQUIRED")
        elif nonfixed:
            disposition = "HOLD_NON_FIXED_USD_REWARD"
            reasons.append("USD_REWARD_NOT_FIXED_GUARANTEED_AMOUNT")
        elif native_non_usd and amount is not None:
            disposition = "HOLD_MIXED_REWARD_CURRENCY"
            reasons.append("MIXED_USD_AND_NATIVE_TOKEN_REWARD")
        elif amount is None:
            disposition = "HOLD_NO_FIXED_USD_REWARD"
            reasons.append(
                "NATIVE_NON_USD_REWARD_ONLY"
                if native_non_usd
                else "FIXED_USD_REWARD_NOT_OBSERVED"
            )
        elif amount >= active_floor:
            disposition = "ACTIVE_REVIEW"
            route = main_route
        elif amount >= pile_floor:
            disposition = "PILE_SAVE_UP"
            route = pile_route
        else:
            disposition = "PRUNE_BELOW_DOLLAR_FLOOR"
            reasons.append("FIXED_USD_REWARD_BELOW_10")

        source_projection = _safe_preflight_projection(preflight)
        body = {
            "schema": _RECEIPT_SCHEMA,
            "identity": {
                "repo": repo_norm,
                "issue_number": issue_num,
                "canonical_issue_url": f"https://github.com/{repo_norm}/issues/{issue_num}",
            },
            "source": {
                "kind": "LIVE_GITHUB_PREFLIGHT",
                "issue_generation_sha256": _sha256_json(marker_after),
                "preflight_sha256": _sha256_json(preflight),
                "preflight": source_projection,
                "repository": repository,
                **({"submission_target_sha256": _sha256_json(submission_target)}
                   if submission_target is not None else {}),
            },
            "economics": {
                "currency": "USD" if amount is not None else None,
                "fixed_amount": _format_decimal(amount) if amount is not None else None,
                "fixed_semantics": bool(amount is not None and not nonfixed),
                "active_floor": "15",
                "pile_floor": "10",
            },
            "disposition": disposition,
            "route": route,
            "reason_codes": reasons,
            "authority": dict(_AUTHORITY),
        }
        return {**body, "receipt_sha256": _sha256_json(body)}

    def verify_live_cash_receipt(
        receipt: dict[str, Any],
        token: str | None = None,
        *,
        session: Any = requests,
        max_pages: int = 10,
        saturation_threshold: int = 4,
        operator_login: str | None = None,
        submission_target: dict[str, str] | None = None,
    ) -> bool:
        """Re-read GitHub; historical self-replay is intentionally insufficient."""
        if not isinstance(receipt, dict) or receipt.get("schema") != _RECEIPT_SCHEMA:
            return False
        if receipt.get("authority") != _AUTHORITY:
            return False
        digest = receipt.get("receipt_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return False
        body = dict(receipt)
        body.pop("receipt_sha256", None)
        if _sha256_json(body) != digest:
            return False
        identity = receipt.get("identity")
        if not isinstance(identity, dict):
            return False
        try:
            if submission_target is not None:
                submission_target = validate_submission_target(
                    submission_target,
                    f"https://github.com/{identity.get('repo')}/issues/{identity.get('issue_number')}",
                )
            target_digest = (
                _sha256_json(submission_target) if submission_target is not None else None
            )
            source = receipt.get("source")
            if not isinstance(source, dict) or source.get("submission_target_sha256") != target_digest:
                return False
            current = evaluate_live_cash_admission(
                identity.get("repo"),
                identity.get("issue_number"),
                token,
                session=session,
                max_pages=max_pages,
                saturation_threshold=saturation_threshold,
                operator_login=operator_login,
                submission_target=submission_target,
            )
        except (LiveCashAdmissionError, ValueError, TypeError):
            return False
        return current == receipt

    return evaluate_live_cash_admission, verify_live_cash_receipt


evaluate_live_cash_admission, verify_live_cash_receipt = _build_api()


def format_summary(receipt: dict[str, Any]) -> str:
    identity = receipt["identity"]
    amount = receipt["economics"]["fixed_amount"] or "none"
    reasons = ",".join(receipt["reason_codes"]) or "none"
    return (
        f"{identity['repo']}#{identity['issue_number']} "
        f"disposition={receipt['disposition']} amount_usd={amount} "
        f"route={receipt['route'] or 'none'} reasons={reasons}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Live source-bound USD bounty admission; caller supplies no reward amount."
    )
    parser.add_argument("repo", help="GitHub repository in owner/name form")
    parser.add_argument("number", type=int, help="GitHub issue number")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--saturation-threshold", type=int, default=4)
    parser.add_argument("--operator-login")
    parser.add_argument(
        "--submission-target", type=Path,
        help="JSON file containing an explicit source-bound submission target",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        submission_target = None
        if args.submission_target is not None:
            submission_target = json.loads(args.submission_target.read_text(encoding="utf-8"))
            if type(submission_target) is not dict:
                raise ValueError("submission-target JSON must contain an object")
        receipt = evaluate_live_cash_admission(
            args.repo,
            args.number,
            max_pages=args.max_pages,
            saturation_threshold=args.saturation_threshold,
            operator_login=args.operator_login,
            submission_target=submission_target,
        )
    except (LiveCashAdmissionError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, indent=2, sort_keys=True) if args.json else format_summary(receipt))
    return 0 if receipt["disposition"] in {"ACTIVE_REVIEW", "PILE_SAVE_UP"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
