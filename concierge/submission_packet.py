# SPDX-License-Identifier: MIT
"""Build evidence-bound, human-only bounty submission packets.

This layer is deliberately downstream of the qualified opportunity portfolio.
It never posts a claim, opens a pull request, establishes maintainer acceptance,
or infers earned/settled cash. It converts one selected portfolio row plus
explicit finished-work evidence into a deterministic human-review packet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit


class SubmissionPacketInputError(ValueError):
    """Raised when request-level authority or structure is unreliable."""


_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GITHUB_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAX_TEXT = 1024
_MAX_PATHS = 256
_MAX_TESTS = 128
_MAX_ACCEPTANCE = 128
_MAX_DECIMAL_TEXT = 128


def _strict_github_url(value: Any, kind: str) -> tuple[str, str, int]:
    if type(value) is not str or not value or len(value) > _MAX_TEXT:
        raise SubmissionPacketInputError(f"{kind} URL must be a non-empty string")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise SubmissionPacketInputError(f"{kind} URL port is invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SubmissionPacketInputError(f"{kind} URL must be canonical github.com HTTPS")
    parts = parsed.path.split("/")
    marker = "issues" if kind == "issue" else "pull"
    if len(parts) != 5 or parts[0] != "" or parts[3] != marker:
        raise SubmissionPacketInputError(f"{kind} URL has invalid path shape")
    owner, repo, raw_number = parts[1], parts[2], parts[4]
    if not _GITHUB_NAME_RE.fullmatch(owner) or not _GITHUB_NAME_RE.fullmatch(repo):
        raise SubmissionPacketInputError(f"{kind} URL repository identity is invalid")
    if owner in {".", ".."} or repo in {".", ".."}:
        raise SubmissionPacketInputError(f"{kind} URL repository identity is invalid")
    if not raw_number.isdigit() or int(raw_number) <= 0 or str(int(raw_number)) != raw_number:
        raise SubmissionPacketInputError(f"{kind} URL number is invalid")
    canonical = f"https://github.com/{owner}/{repo}/{marker}/{raw_number}"
    if value != canonical:
        raise SubmissionPacketInputError(f"{kind} URL must use canonical spelling")
    return owner, repo, int(raw_number)


def _positive_decimal_text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip() or len(value) > _MAX_DECIMAL_TEXT:
        raise SubmissionPacketInputError(f"{name} must be a bounded decimal string")
    text = value.strip()
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise SubmissionPacketInputError(f"{name} must be a finite decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise SubmissionPacketInputError(f"{name} must be greater than zero")
    parts = parsed.as_tuple()
    if len(parts.digits) > 64 or abs(parts.exponent) > 64:
        raise SubmissionPacketInputError(f"{name} exceeds supported representation")
    return text


def _clean_path(value: Any) -> str:
    if type(value) is not str or not value or len(value) > _MAX_TEXT:
        raise SubmissionPacketInputError("repository paths must be non-empty bounded strings")
    if "\\" in value or "\x00" in value or any(ord(ch) < 32 for ch in value):
        raise SubmissionPacketInputError("repository paths must use clean POSIX syntax")
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("./") or value.endswith("/"):
        raise SubmissionPacketInputError("repository paths must be canonical relative paths")
    if any(part in ("", ".", "..") for part in path.parts):
        raise SubmissionPacketInputError("repository paths must not traverse or alias")
    canonical = path.as_posix()
    if canonical != value:
        raise SubmissionPacketInputError("repository paths must use canonical spelling")
    return canonical


def _path_list(value: Any, name: str, *, allow_empty: bool = False) -> list[str]:
    if type(value) is not list or len(value) > _MAX_PATHS:
        raise SubmissionPacketInputError(f"{name} must be a bounded list")
    paths = [_clean_path(item) for item in value]
    if not allow_empty and not paths:
        raise SubmissionPacketInputError(f"{name} must not be empty")
    if len(paths) != len(set(paths)):
        raise SubmissionPacketInputError(f"{name} contains duplicate paths")
    return paths


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value or len(value) > _MAX_TEXT:
        raise SubmissionPacketInputError(f"{name} must be a non-empty bounded string")
    if "\x00" in value or any(ord(ch) < 32 and ch not in "\t" for ch in value):
        raise SubmissionPacketInputError(f"{name} contains control characters")
    return value


def _tests(value: Any) -> tuple[list[dict[str, str]], bool]:
    if type(value) is not list or not value or len(value) > _MAX_TESTS:
        raise SubmissionPacketInputError("tests must be a non-empty bounded list")
    result: list[dict[str, str]] = []
    commands: set[str] = set()
    all_pass = True
    for item in value:
        if type(item) is not dict or set(item) != {"command", "outcome"}:
            raise SubmissionPacketInputError("each test must contain only command and outcome")
        command = _text(item["command"], "test command")
        if command in commands:
            raise SubmissionPacketInputError("test commands must be unique")
        commands.add(command)
        outcome = item["outcome"]
        if outcome not in {"PASS", "FAIL", "PENDING", "CANCELLED"}:
            raise SubmissionPacketInputError("test outcome is invalid")
        if outcome != "PASS":
            all_pass = False
        result.append({"command": command, "outcome": outcome})
    return result, all_pass


def _acceptance(value: Any) -> tuple[list[dict[str, str]], bool]:
    if type(value) is not list or not value or len(value) > _MAX_ACCEPTANCE:
        raise SubmissionPacketInputError("acceptance_checks must be a non-empty bounded list")
    result: list[dict[str, str]] = []
    ids: set[str] = set()
    all_pass = True
    for item in value:
        if type(item) is not dict or set(item) != {"criterion_id", "status"}:
            raise SubmissionPacketInputError(
                "each acceptance check must contain only criterion_id and status"
            )
        criterion = _text(item["criterion_id"], "criterion_id")
        if criterion in ids:
            raise SubmissionPacketInputError("acceptance criterion IDs must be unique")
        ids.add(criterion)
        status = item["status"]
        if status not in {"PASS", "FAIL", "UNKNOWN"}:
            raise SubmissionPacketInputError("acceptance status is invalid")
        if status != "PASS":
            all_pass = False
        result.append({"criterion_id": criterion, "status": status})
    return result, all_pass


def _bind_evidence(entry: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "canonical_source_url",
        "pull_request_url",
        "head_sha",
        "changed_paths",
        "allowed_paths",
        "tests",
        "evidence_sha256",
        "acceptance_checks",
    }
    if set(entry) != allowed_keys:
        raise SubmissionPacketInputError("evidence entry has missing or undeclared fields")
    source = entry["canonical_source_url"]
    source_owner, source_repo, _ = _strict_github_url(source, "issue")
    pr_url = entry["pull_request_url"]
    pr_owner, pr_repo, pr_number = _strict_github_url(pr_url, "pull")
    if (source_owner.casefold(), source_repo.casefold()) != (
        pr_owner.casefold(),
        pr_repo.casefold(),
    ):
        raise SubmissionPacketInputError(
            "pull request repository must match canonical source repository"
        )
    head_sha = entry["head_sha"]
    if type(head_sha) is not str or not _SHA40_RE.fullmatch(head_sha):
        raise SubmissionPacketInputError("head_sha must be exactly 40 lowercase hex characters")
    evidence_sha = entry["evidence_sha256"]
    if type(evidence_sha) is not str or not _SHA256_RE.fullmatch(evidence_sha):
        raise SubmissionPacketInputError("evidence_sha256 must be exactly 64 lowercase hex characters")
    changed = _path_list(entry["changed_paths"], "changed_paths")
    allowed = _path_list(entry["allowed_paths"], "allowed_paths")
    tests, tests_pass = _tests(entry["tests"])
    acceptance, acceptance_pass = _acceptance(entry["acceptance_checks"])
    changed_outside_allowlist = sorted(set(changed) - set(allowed))
    return {
        "canonical_source_url": source,
        "pull_request_url": pr_url,
        "pull_request_repo": f"{pr_owner}/{pr_repo}",
        "pull_request_number": pr_number,
        "head_sha": head_sha,
        "changed_paths": sorted(changed),
        "allowed_paths": sorted(allowed),
        "tests": tests,
        "evidence_sha256": evidence_sha,
        "acceptance_checks": acceptance,
        "_changed_outside_allowlist": changed_outside_allowlist,
        "_tests_pass": tests_pass,
        "_acceptance_pass": acceptance_pass,
    }


def _validate_portfolio(portfolio: Any) -> list[dict[str, Any]]:
    if type(portfolio) is not dict:
        raise SubmissionPacketInputError("portfolio must be an object")
    if portfolio.get("schema") != "qualified-opportunity-portfolio/v1":
        raise SubmissionPacketInputError("portfolio schema is not authoritative")
    authority = portfolio.get("authority")
    if type(authority) is not dict or authority.get("cash_claim") is not False:
        raise SubmissionPacketInputError("portfolio must explicitly disclaim cash authority")
    selected = portfolio.get("selected")
    if type(selected) is not list:
        raise SubmissionPacketInputError("portfolio selected rows are missing")
    selected_count = portfolio.get("selected_count")
    if isinstance(selected_count, bool) or not isinstance(selected_count, int):
        raise SubmissionPacketInputError("portfolio selected_count is invalid")
    if selected_count != len(selected):
        raise SubmissionPacketInputError("portfolio selected_count does not match selected rows")
    seen: set[str] = set()
    checked: list[dict[str, Any]] = []
    for row in selected:
        if type(row) is not dict:
            raise SubmissionPacketInputError("selected portfolio rows must be objects")
        source = row.get("canonical_source_url")
        _strict_github_url(source, "issue")
        if source in seen:
            raise SubmissionPacketInputError("portfolio contains duplicate canonical sources")
        seen.add(source)
        reward = _positive_decimal_text(row.get("advertised_reward_usd"), "advertised_reward_usd")
        row_authority = row.get("authority")
        if (
            type(row_authority) is not dict
            or row_authority.get("reward") != "advertised_only"
            or row_authority.get("revenue") != "not_earned_or_settled_by_this_receipt"
        ):
            raise SubmissionPacketInputError("selected row revenue authority is invalid")
        checked.append({"canonical_source_url": source, "advertised_reward_usd": reward})
    return checked


def build_submission_packet(portfolio: Any, evidence: Any) -> dict[str, Any]:
    """Build deterministic, human-only submission readiness packets."""
    selected = _validate_portfolio(portfolio)
    if type(evidence) is not list or len(evidence) > max(len(selected), 1) + 128:
        raise SubmissionPacketInputError("evidence must be a bounded list")

    evidence_by_source: dict[str, dict[str, Any]] = {}
    for raw in evidence:
        if type(raw) is not dict:
            raise SubmissionPacketInputError("evidence entries must be objects")
        bound = _bind_evidence(raw)
        source = bound["canonical_source_url"]
        if source in evidence_by_source:
            raise SubmissionPacketInputError("duplicate evidence for canonical source")
        evidence_by_source[source] = bound

    selected_sources = {row["canonical_source_url"] for row in selected}
    extras = sorted(set(evidence_by_source) - selected_sources)
    if extras:
        raise SubmissionPacketInputError("evidence contains an unselected canonical source")

    packets: list[dict[str, Any]] = []
    for row in selected:
        source = row["canonical_source_url"]
        bound = evidence_by_source.get(source)
        reasons: list[str] = []
        if bound is None:
            reasons.append("EVIDENCE_MISSING")
            public_evidence = None
        else:
            if bound["_changed_outside_allowlist"]:
                reasons.append("CHANGED_PATH_OUTSIDE_ALLOWLIST")
            if not bound["_tests_pass"]:
                reasons.append("TESTS_NOT_ALL_PASS")
            if not bound["_acceptance_pass"]:
                reasons.append("ACCEPTANCE_CHECKS_NOT_ALL_PASS")
            public_evidence = {
                key: value
                for key, value in bound.items()
                if not key.startswith("_") and key != "canonical_source_url"
            }

        disposition = "READY_FOR_HUMAN_SUBMISSION" if not reasons else "HOLD"
        packet_core = {
            "canonical_source_url": source,
            "advertised_reward_usd": row["advertised_reward_usd"],
            "disposition": disposition,
            "reason_codes": reasons,
            "evidence": public_evidence,
            "authority": {
                "submission": "human_only",
                "reward": "advertised_only",
                "acceptance": "not_inferred",
                "payout": "not_inferred",
                "cash_claim": False,
            },
        }
        canonical = json.dumps(packet_core, sort_keys=True, separators=(",", ":")).encode("utf-8")
        packet_core["packet_sha256"] = hashlib.sha256(canonical).hexdigest()
        packets.append(packet_core)

    ready_count = sum(p["disposition"] == "READY_FOR_HUMAN_SUBMISSION" for p in packets)
    hold_count = len(packets) - ready_count
    return {
        "schema": "bounty-submission-packet/v1",
        "selected_count": len(selected),
        "ready_count": ready_count,
        "hold_count": hold_count,
        "packets": packets,
        "authority": {
            "submission": "human_only",
            "external_post_performed": False,
            "acceptance_inferred": False,
            "payout_inferred": False,
            "cash_claim": False,
        },
    }


def format_summary(result: dict[str, Any]) -> str:
    return (
        f"selected={result['selected_count']} ready={result['ready_count']} "
        f"hold={result['hold_count']} external_post=false cash_claim=false"
    )


def _load_request(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        value = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    if type(value) is not dict:
        raise SubmissionPacketInputError("request JSON must contain an object")
    if set(value) != {"portfolio", "evidence"}:
        raise SubmissionPacketInputError("request must contain exactly portfolio and evidence")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.submission_packet",
        description="Build human-only evidence-bound packets for selected paid work.",
    )
    parser.add_argument("request", help="JSON request path, or - for stdin")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    try:
        request = _load_request(args.request)
        result = build_submission_packet(request["portfolio"], request["evidence"])
    except (OSError, json.JSONDecodeError, SubmissionPacketInputError) as exc:
        parser.error(str(exc))
    if args.summary:
        print(format_summary(result))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["selected_count"] > 0 and result["hold_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
