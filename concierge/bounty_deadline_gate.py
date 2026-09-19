# SPDX-License-Identifier: MIT
"""Fail-closed advisory gate for bounty deadline evidence.

A GitHub issue or provider row can remain OPEN after its sponsor deadline has
elapsed. This module binds one canonical issue observation to frozen deadline
source evidence and emits a deterministic advisory receipt. It performs no
network access and grants no provider, repository-write, submission, reward,
wallet, or payment authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class BountyDeadlineInputError(ValueError):
    """Raised when deadline evidence is malformed, stale, or ambiguous."""


_SCHEMA = "bounty-deadline-gate/v1"
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_GITHUB_ISSUE_PATH_RE = re.compile(
    r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)/?$"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ALLOWED_DEADLINE_KINDS = frozenset({"DATE", "INSTANT"})
_ALLOWED_BASE_AUTHORITIES = frozenset(
    {"ISSUE_BODY", "OFFICIAL_PROVIDER", "REPO_OWNER", "REPO_MEMBER"}
)
_ALLOWED_EXTENSION_AUTHORITIES = frozenset(
    {"OFFICIAL_PROVIDER", "REPO_OWNER", "REPO_MEMBER"}
)
_MAX_URL_CHARS = 2048


_AUTHORITY = {
    "advisory_only": True,
    "provider_application_authority": False,
    "repository_write_authority": False,
    "submission_authority": False,
    "reward_award_authority": False,
    "payment_or_wallet_authority": False,
}


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise BountyDeadlineInputError(f"{field} must be an object")
    return value


def _require_exact_keys(
    value: dict[str, Any], field: str, required: set[str], optional: set[str] | None = None
) -> None:
    optional = optional or set()
    keys = set(value)
    missing = required - keys
    extra = keys - required - optional
    if missing:
        raise BountyDeadlineInputError(
            f"{field} missing required keys: {','.join(sorted(missing))}"
        )
    if extra:
        raise BountyDeadlineInputError(
            f"{field} contains unknown keys: {','.join(sorted(extra))}"
        )


def _require_string(value: Any, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise BountyDeadlineInputError(f"{field} must be a non-empty trimmed string")
    return value


def _require_bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise BountyDeadlineInputError(f"{field} must be a boolean")
    return value


def _require_sha256(value: Any, field: str) -> str:
    digest = _require_string(value, field)
    if _SHA256_RE.fullmatch(digest) is None:
        raise BountyDeadlineInputError(f"{field} must be a lowercase SHA-256 hex digest")
    return digest


def _strict_url_parts(value: Any, field: str) -> tuple[str, str]:
    source = _require_string(value, field)
    if len(source) > _MAX_URL_CHARS:
        raise BountyDeadlineInputError(f"{field} exceeds {_MAX_URL_CHARS} characters")
    if any(character.isspace() or ord(character) == 0x7F for character in source):
        raise BountyDeadlineInputError(f"{field} must not contain whitespace")
    if "\\" in source or "%" in source:
        raise BountyDeadlineInputError(
            f"{field} must not contain backslash or encoded path aliases"
        )
    if "?" in source or "#" in source:
        raise BountyDeadlineInputError(
            f"{field} must not contain query or fragment delimiters"
        )

    try:
        parsed = urlsplit(source)
        port = parsed.port
    except ValueError as exc:
        raise BountyDeadlineInputError(f"{field} must be a valid URL") from exc
    if parsed.scheme.casefold() != "https":
        raise BountyDeadlineInputError(f"{field} must use https")
    if parsed.username is not None or parsed.password is not None:
        raise BountyDeadlineInputError(f"{field} must not contain userinfo")
    if port is not None:
        raise BountyDeadlineInputError(f"{field} must not contain an explicit port")
    if parsed.query or parsed.fragment:
        raise BountyDeadlineInputError(f"{field} must not contain query or fragment")
    host = (parsed.hostname or "").casefold()
    if not host:
        raise BountyDeadlineInputError(f"{field} must contain a host")
    if parsed.path.startswith("//") or "//" in parsed.path:
        raise BountyDeadlineInputError(f"{field} must not contain repeated separators")
    return host, parsed.path


def _github_issue_identity(value: Any, field: str) -> tuple[str, str, int]:
    host, path = _strict_url_parts(value, field)
    if host not in _GITHUB_HOSTS:
        raise BountyDeadlineInputError(f"{field} must use github.com")
    match = _GITHUB_ISSUE_PATH_RE.fullmatch(path)
    if match is None:
        raise BountyDeadlineInputError(f"{field} is not a canonical GitHub issue URL")
    owner, repo, number = match.groups()
    return owner.casefold(), repo.casefold(), int(number)


def _parse_timestamp(value: Any, field: str) -> datetime:
    raw = _require_string(value, field)
    if not raw.endswith("Z"):
        raise BountyDeadlineInputError(f"{field} must be UTC RFC3339 ending in Z")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise BountyDeadlineInputError(f"{field} must be valid RFC3339") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise BountyDeadlineInputError(f"{field} must be UTC")
    return parsed


def _parse_date(value: Any, field: str) -> date:
    raw = _require_string(value, field)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
        raise BountyDeadlineInputError(f"{field} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise BountyDeadlineInputError(f"{field} must be a valid calendar date") from exc
    if parsed.isoformat() != raw:
        raise BountyDeadlineInputError(f"{field} must use canonical YYYY-MM-DD form")
    return parsed


def _normalize_issue(issue: Any) -> dict[str, Any]:
    issue = _require_object(issue, "issue")
    _require_exact_keys(
        issue,
        "issue",
        {"url", "state", "observed_at", "source_content_sha256"},
    )
    owner, repo, number = _github_issue_identity(issue["url"], "issue.url")
    state = _require_string(issue["state"], "issue.state").upper()
    if state not in {"OPEN", "CLOSED"}:
        raise BountyDeadlineInputError("issue.state must be OPEN or CLOSED")
    observed = _parse_timestamp(issue["observed_at"], "issue.observed_at")
    source_digest = _require_sha256(
        issue["source_content_sha256"], "issue.source_content_sha256"
    )
    return {
        "owner": owner,
        "repo": repo,
        "number": number,
        "url": issue["url"],
        "state": state,
        "observed_at": issue["observed_at"],
        "observed_dt": observed,
        "source_content_sha256": source_digest,
    }


def _normalize_deadline_evidence(
    evidence: Any,
    field: str,
    *,
    allowed_authorities: frozenset[str],
) -> dict[str, Any]:
    evidence = _require_object(evidence, field)
    _require_exact_keys(
        evidence,
        field,
        {
            "kind",
            "value",
            "authority",
            "source_url",
            "source_content_sha256",
            "observed_at",
        },
    )
    kind = _require_string(evidence["kind"], f"{field}.kind").upper()
    if kind not in _ALLOWED_DEADLINE_KINDS:
        raise BountyDeadlineInputError(f"{field}.kind must be DATE or INSTANT")
    authority = _require_string(evidence["authority"], f"{field}.authority").upper()
    if authority not in allowed_authorities:
        allowed = ",".join(sorted(allowed_authorities))
        raise BountyDeadlineInputError(
            f"{field}.authority must be one of {allowed}"
        )
    host, path = _strict_url_parts(evidence["source_url"], f"{field}.source_url")
    source_digest = _require_sha256(
        evidence["source_content_sha256"], f"{field}.source_content_sha256"
    )
    observed = _parse_timestamp(evidence["observed_at"], f"{field}.observed_at")

    if kind == "DATE":
        parsed_value: date | datetime = _parse_date(evidence["value"], f"{field}.value")
    else:
        parsed_value = _parse_timestamp(evidence["value"], f"{field}.value")

    return {
        "kind": kind,
        "value": evidence["value"],
        "parsed_value": parsed_value,
        "authority": authority,
        "source_url": evidence["source_url"],
        "source_host": host,
        "source_path": path,
        "source_content_sha256": source_digest,
        "observed_at": evidence["observed_at"],
        "observed_dt": observed,
    }


def _deadline_relation(deadline: dict[str, Any], evaluated: datetime) -> str:
    """Return CURRENT, ELAPSED, or BOUNDARY_AMBIGUOUS."""
    if deadline["kind"] == "INSTANT":
        instant = deadline["parsed_value"]
        assert isinstance(instant, datetime)
        return "CURRENT" if evaluated <= instant else "ELAPSED"

    day = deadline["parsed_value"]
    assert isinstance(day, date) and not isinstance(day, datetime)
    eval_day = evaluated.date()
    if eval_day < day:
        return "CURRENT"
    if eval_day > day:
        return "ELAPSED"
    # A source that gives only a civil date does not establish a UTC cutoff or
    # sponsor timezone. Refuse to invent a same-day clock boundary.
    return "BOUNDARY_AMBIGUOUS"


def _strictly_later_deadline(new: dict[str, Any], old: dict[str, Any]) -> bool:
    if new["kind"] != old["kind"]:
        # Cross-precision comparisons invite invented time-zone semantics. A
        # sponsor can republish the complete deadline in the same precision.
        return False
    if new["kind"] == "DATE":
        return new["parsed_value"] > old["parsed_value"]
    return new["parsed_value"] > old["parsed_value"]


def compile_bounty_deadline_gate(request: dict[str, Any]) -> dict[str, Any]:
    """Compile frozen deadline evidence into an advisory-only receipt."""
    request = _require_object(request, "request")
    _require_exact_keys(
        request,
        "request",
        {
            "schema",
            "issue",
            "deadline_evidence",
            "extension_evidence",
            "evaluated_at",
            "max_observation_age_seconds",
        },
    )
    if request["schema"] != _SCHEMA:
        raise BountyDeadlineInputError(f"schema must equal {_SCHEMA}")

    issue = _normalize_issue(request["issue"])
    deadline = _normalize_deadline_evidence(
        request["deadline_evidence"],
        "deadline_evidence",
        allowed_authorities=_ALLOWED_BASE_AUTHORITIES,
    )
    extension_raw = request["extension_evidence"]
    extension = None
    if extension_raw is not None:
        extension = _normalize_deadline_evidence(
            extension_raw,
            "extension_evidence",
            allowed_authorities=_ALLOWED_EXTENSION_AUTHORITIES,
        )

    evaluated = _parse_timestamp(request["evaluated_at"], "evaluated_at")
    max_age = request["max_observation_age_seconds"]
    if isinstance(max_age, bool) or not isinstance(max_age, int):
        raise BountyDeadlineInputError(
            "max_observation_age_seconds must be an integer"
        )
    if max_age < 1 or max_age > 86400:
        raise BountyDeadlineInputError(
            "max_observation_age_seconds must be between 1 and 86400"
        )

    observations = [issue["observed_dt"], deadline["observed_dt"]]
    if extension is not None:
        observations.append(extension["observed_dt"])
    if any(observed > evaluated for observed in observations):
        raise BountyDeadlineInputError(
            "evidence observation timestamps must not be after evaluated_at"
        )

    issue_age = int((evaluated - issue["observed_dt"]).total_seconds())
    deadline_age = int((evaluated - deadline["observed_dt"]).total_seconds())
    extension_age = (
        int((evaluated - extension["observed_dt"]).total_seconds())
        if extension is not None
        else None
    )

    reasons: list[str] = []
    if issue["state"] != "OPEN":
        reasons.append("CANONICAL_ISSUE_NOT_OPEN")
    if issue_age > max_age:
        reasons.append("ISSUE_OBSERVATION_STALE")
    if deadline_age > max_age:
        reasons.append("DEADLINE_OBSERVATION_STALE")

    effective = deadline
    extension_status = "NONE"
    if extension is not None:
        if extension_age is not None and extension_age > max_age:
            reasons.append("EXTENSION_OBSERVATION_STALE")
            extension_status = "REJECTED_STALE"
        elif extension["observed_dt"] < deadline["observed_dt"]:
            reasons.append("EXTENSION_PREDATES_BASE_OBSERVATION")
            extension_status = "REJECTED_PREDATES_BASE"
        elif not _strictly_later_deadline(extension, deadline):
            reasons.append("EXTENSION_NOT_STRICTLY_LATER_SAME_PRECISION")
            extension_status = "REJECTED_NOT_LATER"
        else:
            effective = extension
            extension_status = "ACCEPTED"

    relation = _deadline_relation(effective, evaluated)
    if relation == "ELAPSED":
        reasons.append("SPONSOR_DEADLINE_ELAPSED")
    elif relation == "BOUNDARY_AMBIGUOUS":
        reasons.append("DATE_BOUNDARY_CLOCK_AMBIGUOUS")

    if reasons:
        disposition = "HOLD"
        if "SPONSOR_DEADLINE_ELAPSED" in reasons:
            next_action = "REQUIRE_EXPLICIT_SPONSOR_EXTENSION_BEFORE_LABOR"
        elif "DATE_BOUNDARY_CLOCK_AMBIGUOUS" in reasons:
            next_action = "REQUIRE_EXACT_CUTOFF_OR_FRESH_SPONSOR_CONFIRMATION"
        else:
            next_action = "REFRESH_OR_REPAIR_DEADLINE_EVIDENCE"
    else:
        disposition = "DEADLINE_CURRENT"
        next_action = "PASS_TO_SEPARATE_ECONOMICS_CARRIER_AND_AUTHORITY_GATES"

    base_public = {
        key: deadline[key]
        for key in (
            "kind",
            "value",
            "authority",
            "source_url",
            "source_content_sha256",
            "observed_at",
        )
    }
    extension_public = None
    if extension is not None:
        extension_public = {
            key: extension[key]
            for key in (
                "kind",
                "value",
                "authority",
                "source_url",
                "source_content_sha256",
                "observed_at",
            )
        }

    body = {
        "schema": _SCHEMA,
        "disposition": disposition,
        "advisory_next_action": next_action,
        "reason_codes": reasons,
        "identity": {
            "owner": issue["owner"],
            "repo": issue["repo"],
            "issue_number": issue["number"],
            "issue_url": issue["url"],
        },
        "issue_observation": {
            "state": issue["state"],
            "observed_at": issue["observed_at"],
            "source_content_sha256": issue["source_content_sha256"],
            "age_seconds": issue_age,
        },
        "deadline": {
            "base_evidence": base_public,
            "extension_evidence": extension_public,
            "extension_status": extension_status,
            "effective_kind": effective["kind"],
            "effective_value": effective["value"],
            "relation_at_evaluation": relation,
            "base_age_seconds": deadline_age,
            "extension_age_seconds": extension_age,
        },
        "evaluation": {
            "evaluated_at": request["evaluated_at"],
            "max_observation_age_seconds": max_age,
        },
        "authority": dict(_AUTHORITY),
        "rule": (
            "OPEN/provider-open state is not deadline-extension authority. "
            "Elapsed or clock-ambiguous sponsor deadlines HOLD until fresh, "
            "strong-authority evidence establishes a current deadline."
        ),
    }
    return {**body, "receipt_sha256": _sha256_json(body)}


def _request_from_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    identity = _require_object(receipt.get("identity"), "identity")
    issue = _require_object(receipt.get("issue_observation"), "issue_observation")
    deadline = _require_object(receipt.get("deadline"), "deadline")
    evaluation = _require_object(receipt.get("evaluation"), "evaluation")
    base = _require_object(deadline.get("base_evidence"), "deadline.base_evidence")
    extension = deadline.get("extension_evidence")
    return {
        "schema": _SCHEMA,
        "issue": {
            "url": identity.get("issue_url"),
            "state": issue.get("state"),
            "observed_at": issue.get("observed_at"),
            "source_content_sha256": issue.get("source_content_sha256"),
        },
        "deadline_evidence": dict(base),
        "extension_evidence": dict(extension) if type(extension) is dict else extension,
        "evaluated_at": evaluation.get("evaluated_at"),
        "max_observation_age_seconds": evaluation.get("max_observation_age_seconds"),
    }


def verify_receipt(receipt: dict[str, Any], *, semantic: bool = True) -> bool:
    """Verify receipt integrity and, by default, recompute deadline semantics."""
    if type(receipt) is not dict:
        return False
    digest = receipt.get("receipt_sha256")
    if type(digest) is not str or _SHA256_RE.fullmatch(digest) is None:
        return False
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    if body.get("authority") != _AUTHORITY:
        return False
    if _sha256_json(body) != digest:
        return False
    if not semantic:
        return True
    try:
        expected = compile_bounty_deadline_gate(_request_from_receipt(receipt))
    except (BountyDeadlineInputError, KeyError, TypeError, ValueError):
        return False
    return expected == receipt


def format_summary(receipt: dict[str, Any]) -> str:
    identity = receipt["identity"]
    reasons = ",".join(receipt["reason_codes"]) or "none"
    return (
        f"disposition={receipt['disposition']} "
        f"issue={identity['owner']}/{identity['repo']}#{identity['issue_number']} "
        f"deadline={receipt['deadline']['effective_value']} "
        f"next={receipt['advisory_next_action']} reasons={reasons} "
        f"receipt_sha256={receipt['receipt_sha256']}"
    )


def _load_json(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        payload = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    return _require_object(payload, "input")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bounty_deadline_gate",
        description=(
            "Compile or verify an advisory-only bounty deadline receipt. "
            "This command performs no network or provider mutation."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("snapshot", help="snapshot JSON path, or - for stdin")
    compile_parser.add_argument("--json", action="store_true", help="emit full receipt JSON")

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("receipt", help="receipt JSON path, or - for stdin")
    args = parser.parse_args(argv)

    try:
        if args.command == "compile":
            receipt = compile_bounty_deadline_gate(_load_json(args.snapshot))
            if args.json:
                print(json.dumps(receipt, indent=2, sort_keys=True))
            else:
                print(format_summary(receipt))
            return 2 if receipt["disposition"] == "HOLD" else 0

        receipt = _load_json(args.receipt)
        valid = verify_receipt(receipt, semantic=True)
        print("VALID" if valid else "INVALID")
        return 0 if valid else 3
    except (OSError, json.JSONDecodeError, BountyDeadlineInputError) as exc:
        parser.error(str(exc))
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
