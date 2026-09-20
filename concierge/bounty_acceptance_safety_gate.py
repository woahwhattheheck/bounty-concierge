# SPDX-License-Identifier: MIT
"""Fail-closed advisory gate for untrusted bounty acceptance text.

Bounty and provider text is untrusted input. A reward must never become authority
for a contributor to disclose hidden instructions, private reasoning, secrets,
auth/session material, or private runtime configuration. This module scans frozen
acceptance text and emits a deterministic advisory receipt without echoing the raw
text into the receipt.

The gate performs no network access and grants no provider, repository-write,
submission, reward, wallet, or payment authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit


class BountyAcceptanceSafetyInputError(ValueError):
    """Raised when acceptance evidence is malformed or internally inconsistent."""


SCHEMA = "bounty-acceptance-safety-gate/v1"
RECEIPT_SCHEMA = "bounty-acceptance-safety-gate-receipt/v1"

_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_GITHUB_ISSUE_PATH_RE = re.compile(
    r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)/?$"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MAX_URL_CHARS = 2048
_MAX_TEXT_CHARS = 250_000
_MAX_SOURCE_AGE_SECONDS = 86_400

_AUTHORITY = {
    "advisory_only": True,
    "provider_application_authority": False,
    "repository_write_authority": False,
    "submission_authority": False,
    "reward_award_authority": False,
    "payment_or_wallet_authority": False,
}

# Classification is deliberately narrow: a sensitive target alone does not hold a
# bounty. A non-negated disclosure verb must occur in the same sentence/line and
# within a bounded distance of the sensitive target.
_ACTION_RE = re.compile(
    r"\b(?:provide|include|paste|print|dump|upload|attach|expose|reveal|submit|"
    r"commit|write|publish|send|return|show|list|record|contain|copy|disclose)\b"
)
_NEGATION_RE = re.compile(
    r"(?:\bdo\s+not\b|\bdon['’]t\b|\bnever\b|\bmust\s+not\b|"
    r"\bshould\s+not\b|\bwithout\b|\bno\s+need\s+to\b)\s*(?:\w+\s+){0,3}$"
)
_SEGMENT_SPLIT_RE = re.compile(r"[\n\r.!?;]+")

_CATEGORY_PATTERNS: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    (
        "REQUESTS_HIDDEN_INSTRUCTIONS",
        (
            re.compile(r"\bsystem\s+(?:prompt|message|instructions?)\b"),
            re.compile(r"\bdeveloper\s+(?:message|instructions?|prompt)\b"),
            re.compile(r"\bhidden\s+(?:prompt|instructions?|rules?)\b"),
            re.compile(r"\bpre[- ]session\s+(?:instructions?|rules?|configuration)\b"),
            re.compile(r"\binitial\s+(?:system\s+)?(?:instructions?|prompt)\b"),
            re.compile(
                r"\b(?:rules?|guidelines?)\b.{0,80}\b(?:configuration|config)\b"
                r".{0,80}\b(?:loaded|provided)\b.{0,40}\b(?:before|prior)\b"
            ),
        ),
    ),
    (
        "REQUESTS_PRIVATE_REASONING",
        (
            re.compile(r"\bchain[- ]of[- ]thought\b"),
            re.compile(r"\bprivate\s+reasoning\b"),
            re.compile(r"\bhidden\s+reasoning\b"),
            re.compile(r"\binternal\s+reasoning\s+(?:trace|steps?)\b"),
        ),
    ),
    (
        "REQUESTS_SECRET_AUTH_MATERIAL",
        (
            re.compile(r"\bapi[-_ ]?keys?\b"),
            re.compile(r"\b(?:access|refresh|auth|bearer)\s+tokens?\b"),
            re.compile(r"\bclient\s+secrets?\b"),
            re.compile(r"\bpasswords?\b"),
            re.compile(r"\bsession\s+(?:cookies?|tokens?|credentials?)\b"),
            re.compile(r"\boauth\s+(?:tokens?|secrets?|credentials?)\b"),
            re.compile(r"\bssh\s+private\s+keys?\b"),
            re.compile(r"\bprivate\s+keys?\b"),
            re.compile(r"\bauthentication\s+credentials?\b"),
            re.compile(r"\blogin\s+credentials?\b"),
        ),
    ),
    (
        "REQUESTS_PRIVATE_RUNTIME_CONTEXT",
        (
            re.compile(r"\benvironment\s+variables?\b"),
            re.compile(r"\benv\s+vars?\b"),
            re.compile(r"\b(?:full|complete|entire)\s+(?:process\s+)?environment\b"),
            re.compile(r"\bprocess\s+environment\b"),
            re.compile(r"\b(?:private|internal|runtime|loaded)\s+configuration\b"),
            re.compile(r"\bplatform[- ]config\b"),
            re.compile(r"(?<!\w)\.env\s+(?:file|contents?)\b"),
            re.compile(r"\b(?:full|complete)\s+environment\s+metadata\b"),
        ),
    ),
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise BountyAcceptanceSafetyInputError(f"{field} must be an object")
    return value


def _require_exact_keys(value: dict[str, Any], field: str, required: set[str]) -> None:
    keys = set(value)
    missing = required - keys
    extra = keys - required
    if missing:
        raise BountyAcceptanceSafetyInputError(
            f"{field} missing required keys: {','.join(sorted(missing))}"
        )
    if extra:
        raise BountyAcceptanceSafetyInputError(
            f"{field} contains unknown keys: {','.join(sorted(extra))}"
        )


def _require_string(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise BountyAcceptanceSafetyInputError(f"{field} must be a string")
    if not allow_empty and (not value or value != value.strip()):
        raise BountyAcceptanceSafetyInputError(
            f"{field} must be a non-empty trimmed string"
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BountyAcceptanceSafetyInputError(
            f"{field} must be valid Unicode encodable as UTF-8"
        ) from exc
    return value


def _require_sha256(value: Any, field: str) -> str:
    digest = _require_string(value, field)
    if _SHA256_RE.fullmatch(digest) is None:
        raise BountyAcceptanceSafetyInputError(
            f"{field} must be a lowercase SHA-256 hex digest"
        )
    return digest


def _strict_url_parts(value: Any, field: str) -> tuple[str, str]:
    source = _require_string(value, field)
    if len(source) > _MAX_URL_CHARS:
        raise BountyAcceptanceSafetyInputError(
            f"{field} exceeds {_MAX_URL_CHARS} characters"
        )
    if any(character.isspace() or ord(character) == 0x7F for character in source):
        raise BountyAcceptanceSafetyInputError(f"{field} must not contain whitespace")
    if "\\" in source or "%" in source:
        raise BountyAcceptanceSafetyInputError(
            f"{field} must not contain backslash or encoded path aliases"
        )
    if "?" in source or "#" in source:
        raise BountyAcceptanceSafetyInputError(
            f"{field} must not contain query or fragment delimiters"
        )
    try:
        parsed = urlsplit(source)
        port = parsed.port
    except ValueError as exc:
        raise BountyAcceptanceSafetyInputError(f"{field} must be a valid URL") from exc
    if parsed.scheme.casefold() != "https":
        raise BountyAcceptanceSafetyInputError(f"{field} must use https")
    if parsed.username is not None or parsed.password is not None:
        raise BountyAcceptanceSafetyInputError(f"{field} must not contain userinfo")
    if port is not None:
        raise BountyAcceptanceSafetyInputError(
            f"{field} must not contain an explicit port"
        )
    if parsed.query or parsed.fragment:
        raise BountyAcceptanceSafetyInputError(
            f"{field} must not contain query or fragment"
        )
    host = (parsed.hostname or "").casefold()
    if not host:
        raise BountyAcceptanceSafetyInputError(f"{field} must contain a host")
    return host, parsed.path


def _github_issue_identity(value: Any, field: str) -> tuple[str, str, int]:
    host, path = _strict_url_parts(value, field)
    if host not in _GITHUB_HOSTS:
        raise BountyAcceptanceSafetyInputError(f"{field} must use github.com")
    match = _GITHUB_ISSUE_PATH_RE.fullmatch(path)
    if match is None:
        raise BountyAcceptanceSafetyInputError(
            f"{field} is not a canonical GitHub issue URL"
        )
    owner, repo, number = match.groups()
    return owner.casefold(), repo.casefold(), int(number)


def _parse_timestamp(value: Any, field: str) -> datetime:
    raw = _require_string(value, field)
    if not raw.endswith("Z"):
        raise BountyAcceptanceSafetyInputError(
            f"{field} must be UTC RFC3339 ending in Z"
        )
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise BountyAcceptanceSafetyInputError(
            f"{field} must be valid RFC3339"
        ) from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise BountyAcceptanceSafetyInputError(f"{field} must be UTC")
    return parsed


def _normalize_for_scan(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _action_positions(segment: str) -> list[int]:
    positions: list[int] = []
    for match in _ACTION_RE.finditer(segment):
        prefix = segment[max(0, match.start() - 48) : match.start()]
        if _NEGATION_RE.search(prefix):
            continue
        positions.append(match.start())
    return positions


def _within_bound(action_positions: Iterable[int], start: int, end: int) -> bool:
    return any(min(abs(pos - start), abs(pos - end)) <= 220 for pos in action_positions)


def _classify_text(text: str) -> tuple[list[str], dict[str, int]]:
    normalized = _normalize_for_scan(text)
    # Protect the leading dot in .env tokens from sentence splitting.  The
    # sentinel is restored before action/target matching, so requirements such
    # as "upload the .env file" remain detectable without joining unrelated
    # ordinary sentences.
    normalized = normalized.replace(".env", "__bounty_dot_env__")
    counts: dict[str, int] = {category: 0 for category, _ in _CATEGORY_PATTERNS}
    for segment in _SEGMENT_SPLIT_RE.split(normalized):
        segment = segment.replace("__bounty_dot_env__", ".env")
        segment = " ".join(segment.split())
        if not segment:
            continue
        action_positions = _action_positions(segment)
        if not action_positions:
            continue
        for category, patterns in _CATEGORY_PATTERNS:
            category_hit = False
            for pattern in patterns:
                for match in pattern.finditer(segment):
                    if _within_bound(action_positions, match.start(), match.end()):
                        counts[category] += 1
                        category_hit = True
                        break
                if category_hit:
                    break
    reasons = [category for category, _ in _CATEGORY_PATTERNS if counts[category] > 0]
    return reasons, counts


def compile_bounty_acceptance_safety_gate(request: dict[str, Any]) -> dict[str, Any]:
    """Compile frozen acceptance text into an advisory-only safety receipt."""
    request = _require_object(request, "request")
    _require_exact_keys(
        request,
        "request",
        {
            "schema",
            "issue_url",
            "source_url",
            "source_text",
            "source_content_sha256",
            "observed_at",
            "evaluated_at",
        },
    )
    if request["schema"] != SCHEMA:
        raise BountyAcceptanceSafetyInputError(f"schema must equal {SCHEMA}")

    owner, repo, issue_number = _github_issue_identity(request["issue_url"], "issue_url")
    _strict_url_parts(request["source_url"], "source_url")
    source_text = _require_string(request["source_text"], "source_text", allow_empty=True)
    if len(source_text) > _MAX_TEXT_CHARS:
        raise BountyAcceptanceSafetyInputError(
            f"source_text exceeds {_MAX_TEXT_CHARS} characters"
        )
    source_bytes = source_text.encode("utf-8")
    expected_digest = _require_sha256(
        request["source_content_sha256"], "source_content_sha256"
    )
    actual_digest = _sha256_bytes(source_bytes)
    if actual_digest != expected_digest:
        raise BountyAcceptanceSafetyInputError(
            "source_content_sha256 does not match source_text bytes"
        )

    observed = _parse_timestamp(request["observed_at"], "observed_at")
    evaluated = _parse_timestamp(request["evaluated_at"], "evaluated_at")
    if observed > evaluated:
        raise BountyAcceptanceSafetyInputError(
            "observed_at must not be after evaluated_at"
        )
    age_seconds = int((evaluated - observed).total_seconds())

    reasons, counts = _classify_text(source_text)
    if age_seconds > _MAX_SOURCE_AGE_SECONDS:
        reasons = ["SOURCE_OBSERVATION_STALE", *reasons]

    if reasons:
        disposition = "HOLD_UNTRUSTED_ACCEPTANCE_TEXT"
        if any(reason.startswith("REQUESTS_") for reason in reasons):
            next_action = "REQUIRE_SANITIZED_ACCEPTANCE_CRITERIA_WITHOUT_PRIVATE_CONTEXT_DEMANDS"
        else:
            next_action = "REFRESH_ACCEPTANCE_TEXT_EVIDENCE"
    else:
        disposition = "ACCEPTANCE_TEXT_CLEAR"
        next_action = "PASS_TO_SEPARATE_ECONOMICS_CLAIM_AND_SOURCE_GATES"

    body = {
        "schema": RECEIPT_SCHEMA,
        "disposition": disposition,
        "advisory_next_action": next_action,
        "identity": {
            "owner": owner,
            "repo": repo,
            "issue_number": issue_number,
            "issue_url": request["issue_url"],
        },
        "source": {
            "url": request["source_url"],
            "content_sha256": actual_digest,
            "observed_at": request["observed_at"],
            "evaluated_at": request["evaluated_at"],
            "age_seconds": age_seconds,
            "text_bytes": len(source_bytes),
        },
        "reason_codes": reasons,
        "match_counts": {key: counts[key] for key in sorted(counts)},
        "authority": dict(_AUTHORITY),
    }
    return {**body, "receipt_sha256": _sha256_json(body)}


def verify_bounty_acceptance_safety_receipt(
    receipt: dict[str, Any], source_text: str
) -> bool:
    """Verify receipt integrity and recompile semantics from the original source text."""
    if type(receipt) is not dict or type(source_text) is not str:
        return False
    if receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("authority") != _AUTHORITY:
        return False
    digest = receipt.get("receipt_sha256")
    if type(digest) is not str or _SHA256_RE.fullmatch(digest) is None:
        return False
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    try:
        if _sha256_json(body) != digest:
            return False
        source = _require_object(receipt.get("source"), "receipt.source")
        identity = _require_object(receipt.get("identity"), "receipt.identity")
        request = {
            "schema": SCHEMA,
            "issue_url": identity["issue_url"],
            "source_url": source["url"],
            "source_text": source_text,
            "source_content_sha256": source["content_sha256"],
            "observed_at": source["observed_at"],
            "evaluated_at": source["evaluated_at"],
        }
        expected = compile_bounty_acceptance_safety_gate(request)
    except (BountyAcceptanceSafetyInputError, KeyError, TypeError, ValueError):
        return False
    return expected == receipt


def format_summary(receipt: dict[str, Any]) -> str:
    identity = receipt["identity"]
    reasons = ",".join(receipt["reason_codes"]) or "none"
    return (
        f"{identity['owner']}/{identity['repo']}#{identity['issue_number']} "
        f"disposition={receipt['disposition']} reasons={reasons}"
    )


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _loads_strict_json(raw: str) -> Any:
    return json.loads(
        raw,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bounty_acceptance_safety_gate",
        description=(
            "Classify untrusted bounty acceptance text for private-context or secret "
            "disclosure demands. No network access is performed."
        ),
    )
    parser.add_argument("request", help="request JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.request == "-":
            import sys

            raw = sys.stdin.read()
        else:
            raw = Path(args.request).read_text(encoding="utf-8")
        request = _loads_strict_json(raw)
        receipt = compile_bounty_acceptance_safety_gate(request)
    except (OSError, UnicodeError, ValueError, BountyAcceptanceSafetyInputError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(receipt, indent=2, sort_keys=True)
        if args.json
        else format_summary(receipt)
    )
    return 0 if receipt["disposition"] == "ACCEPTANCE_TEXT_CLEAR" else 2


if __name__ == "__main__":
    raise SystemExit(main())
