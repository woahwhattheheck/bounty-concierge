# SPDX-License-Identifier: MIT
"""Fail-closed owner-review authority for vendor-funded security patch pursuits.

This carrier exists for reward programs where compensation evidence lives in a
separate official program source rather than on the upstream GitHub issue.
It does not weaken the ordinary per-issue bounty gate, browse, exploit, submit,
accept terms, spend, or assert an award/payment/revenue event.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SCHEMA_VERSION = "bounty-concierge.vendor-patch-pursuit/v1"
PACKET_VERSION = "bounty-concierge.vendor-patch-pursuit-packet/v1"
RECEIPT_VERSION = "bounty-concierge.vendor-patch-pursuit-receipt/v1"

_CANDIDATE_TTL_SECONDS = 30 * 60
_PROGRAM_TTL_SECONDS = 7 * 24 * 60 * 60
_RESERVATION_OBSERVATION_TTL_SECONDS = 30 * 60
_MAX_RESERVATION_HORIZON_SECONDS = 12 * 60 * 60

_HEX64 = frozenset("0123456789abcdef")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,159}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PROGRAM_STATES = frozenset({"ACTIVE", "PAUSED", "CLOSED", "UNKNOWN"})
_REWARD_STATES = frozenset({"PROGRAM_SCHEDULE", "UNSPECIFIED_BY_SOURCE"})
_ISSUE_STATES = frozenset({"OPEN", "CLOSED", "UNKNOWN"})
_RESERVATION_STATES = frozenset({"ACTIVE", "RELEASED", "UNKNOWN"})
_CONTRIBUTION_ROUTES = frozenset(
    {"MERGED_PR_THEN_CLAIM", "PATCH_THEN_PROGRAM_REVIEW", "PROGRAM_DEFINED"}
)


class VendorPatchPursuitInputError(ValueError):
    """Input is not structurally trustworthy."""


class VendorPatchPursuitVerificationError(ValueError):
    """Receipt or packet verification failed."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha_json(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _obj(value: Any, name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise VendorPatchPursuitInputError(f"{name} must be an object")
    return value


def _keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        raise VendorPatchPursuitInputError(
            f"{name} shape mismatch: missing={sorted(expected - actual)} "
            f"extra={sorted(actual - expected)}"
        )


def _text(value: Any, name: str, limit: int = 512) -> str:
    if type(value) is not str or not value.strip():
        raise VendorPatchPursuitInputError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > limit:
        raise VendorPatchPursuitInputError(f"{name} is too long")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise VendorPatchPursuitInputError(f"{name} contains control characters")
    return value


def _identifier(value: Any, name: str, limit: int = 160) -> str:
    value = _text(value, name, limit)
    if not _ID_RE.fullmatch(value):
        raise VendorPatchPursuitInputError(f"{name} must be a log-safe identifier")
    return value


def _bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise VendorPatchPursuitInputError(f"{name} must be boolean")
    return value


def _int(value: Any, name: str, *, minimum: int = 0, maximum: int = 10**12) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise VendorPatchPursuitInputError(
            f"{name} must be an integer in [{minimum}, {maximum}]"
        )
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name, 64)
    if len(value) != 64 or any(ch not in _HEX64 for ch in value):
        raise VendorPatchPursuitInputError(f"{name} must be a lowercase sha256")
    return value


def _url(value: Any, name: str) -> str:
    value = _text(value, name, 2048)
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise VendorPatchPursuitInputError(
            f"{name} must be a fragment-free https URL without credentials"
        )
    return value


def _time(value: Any, name: str) -> tuple[str, int]:
    value = _text(value, name, 40)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VendorPatchPursuitInputError(
            f"{name} must be canonical second-precision UTC"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise VendorPatchPursuitInputError(f"{name} must be UTC")
    canonical = (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    if value != canonical:
        raise VendorPatchPursuitInputError(
            f"{name} must be canonical second-precision UTC"
        )
    return canonical, int(parsed.timestamp())


def trusted_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _normalize_candidate(raw: Any, now: int) -> tuple[dict[str, Any], list[str]]:
    row = _obj(raw, "candidate")
    _keys(
        row,
        {
            "repository",
            "issue_number",
            "issue_url",
            "issue_state",
            "assignee_count",
            "open_pr_count",
            "pr_search_complete",
            "observed_at",
            "evidence_ref",
            "evidence_sha256",
        },
        "candidate",
    )
    repository = _text(row["repository"], "candidate.repository", 200)
    if not _REPO_RE.fullmatch(repository):
        raise VendorPatchPursuitInputError(
            "candidate.repository must be owner/repository"
        )
    issue_number = _int(
        row["issue_number"], "candidate.issue_number", minimum=1, maximum=2**31 - 1
    )
    issue_url = _url(row["issue_url"], "candidate.issue_url")
    expected_suffix = f"/{repository}/issues/{issue_number}"
    parsed_issue = urlparse(issue_url)
    if (
        parsed_issue.netloc.casefold() != "github.com"
        or parsed_issue.path.rstrip("/") != expected_suffix
        or parsed_issue.query
    ):
        raise VendorPatchPursuitInputError(
            "candidate.issue_url must exactly identify the GitHub issue"
        )
    state = _text(row["issue_state"], "candidate.issue_state", 16).upper()
    if state not in _ISSUE_STATES:
        raise VendorPatchPursuitInputError("candidate.issue_state is unknown")
    observed_at, observed = _time(row["observed_at"], "candidate.observed_at")
    if observed > now:
        raise VendorPatchPursuitInputError("candidate observation is from the future")
    assignee_count = _int(
        row["assignee_count"], "candidate.assignee_count", maximum=10000
    )
    open_pr_count = _int(
        row["open_pr_count"], "candidate.open_pr_count", maximum=100000
    )
    search_complete = _bool(
        row["pr_search_complete"], "candidate.pr_search_complete"
    )
    normalized = {
        "repository": repository,
        "issue_number": issue_number,
        "issue_url": issue_url,
        "issue_state": state,
        "assignee_count": assignee_count,
        "open_pr_count": open_pr_count,
        "pr_search_complete": search_complete,
        "observed_at": observed_at,
        "evidence_ref": _url(row["evidence_ref"], "candidate.evidence_ref"),
        "evidence_sha256": _digest(
            row["evidence_sha256"], "candidate.evidence_sha256"
        ),
    }
    reasons: list[str] = []
    if state != "OPEN":
        reasons.append("CANDIDATE_ISSUE_NOT_OPEN")
    if assignee_count:
        reasons.append("CANDIDATE_ASSIGNED")
    if open_pr_count:
        reasons.append("CANDIDATE_PR_OCCUPIED")
    if not search_complete:
        reasons.append("CANDIDATE_PR_SEARCH_INCOMPLETE")
    if now - observed > _CANDIDATE_TTL_SECONDS:
        reasons.append("CANDIDATE_OBSERVATION_STALE")
    return normalized, reasons


def _normalize_program(
    raw: Any, candidate_repo: str, now: int
) -> tuple[dict[str, Any], list[str]]:
    row = _obj(raw, "program")
    _keys(
        row,
        {
            "program_id",
            "program_name",
            "source_url",
            "status",
            "repository_in_scope",
            "scope_repository",
            "reward_state",
            "currency",
            "min_reward_minor",
            "max_reward_minor",
            "contribution_route",
            "observed_at",
            "evidence_ref",
            "evidence_sha256",
        },
        "program",
    )
    program_id = _identifier(row["program_id"], "program.program_id", 96)
    program_name = _text(row["program_name"], "program.program_name", 200)
    source_url = _url(row["source_url"], "program.source_url")
    status = _text(row["status"], "program.status", 16).upper()
    if status not in _PROGRAM_STATES:
        raise VendorPatchPursuitInputError("program.status is unknown")
    repository_in_scope = _bool(
        row["repository_in_scope"], "program.repository_in_scope"
    )
    scope_repository = _text(
        row["scope_repository"], "program.scope_repository", 200
    )
    if not _REPO_RE.fullmatch(scope_repository):
        raise VendorPatchPursuitInputError(
            "program.scope_repository must be owner/repository"
        )
    if scope_repository != candidate_repo:
        raise VendorPatchPursuitInputError(
            "program.scope_repository must equal candidate.repository"
        )
    reward_state = _text(row["reward_state"], "program.reward_state", 32).upper()
    if reward_state not in _REWARD_STATES:
        raise VendorPatchPursuitInputError("program.reward_state is unknown")
    currency = row["currency"]
    minimum = row["min_reward_minor"]
    maximum = row["max_reward_minor"]
    if reward_state == "PROGRAM_SCHEDULE":
        currency = _text(currency, "program.currency", 3)
        if len(currency) != 3 or not currency.isalpha() or currency != currency.upper():
            raise VendorPatchPursuitInputError(
                "program.currency must be a 3-letter uppercase code"
            )
        minimum = _int(
            minimum, "program.min_reward_minor", minimum=1, maximum=10**15
        )
        maximum = _int(
            maximum, "program.max_reward_minor", minimum=1, maximum=10**15
        )
        if minimum > maximum:
            raise VendorPatchPursuitInputError("program reward range is inverted")
    else:
        if currency is not None or minimum is not None or maximum is not None:
            raise VendorPatchPursuitInputError(
                "unspecified reward source must not carry currency or amounts"
            )
        currency = minimum = maximum = None
    route = _text(
        row["contribution_route"], "program.contribution_route", 40
    ).upper()
    if route not in _CONTRIBUTION_ROUTES:
        raise VendorPatchPursuitInputError(
            "program.contribution_route is unsupported"
        )
    observed_at, observed = _time(row["observed_at"], "program.observed_at")
    if observed > now:
        raise VendorPatchPursuitInputError("program observation is from the future")
    normalized = {
        "program_id": program_id,
        "program_name": program_name,
        "source_url": source_url,
        "status": status,
        "repository_in_scope": repository_in_scope,
        "scope_repository": scope_repository,
        "reward_state": reward_state,
        "currency": currency,
        "min_reward_minor": minimum,
        "max_reward_minor": maximum,
        "contribution_route": route,
        "observed_at": observed_at,
        "evidence_ref": _url(row["evidence_ref"], "program.evidence_ref"),
        "evidence_sha256": _digest(
            row["evidence_sha256"], "program.evidence_sha256"
        ),
    }
    reasons: list[str] = []
    if status != "ACTIVE":
        reasons.append("PROGRAM_NOT_ACTIVE")
    if not repository_in_scope:
        reasons.append("PROGRAM_REPOSITORY_OUT_OF_SCOPE")
    if reward_state != "PROGRAM_SCHEDULE":
        reasons.append("PROGRAM_REWARD_NOT_EVIDENCED")
    if now - observed > _PROGRAM_TTL_SECONDS:
        reasons.append("PROGRAM_OBSERVATION_STALE")
    return normalized, reasons


def _normalize_reservation(
    raw: Any, operator_id: str, now: int
) -> tuple[dict[str, Any], list[str]]:
    row = _obj(raw, "reservation")
    _keys(
        row,
        {
            "reservation_key",
            "owner_id",
            "state",
            "collision_count",
            "observed_at",
            "expires_at",
            "evidence_ref",
            "evidence_sha256",
        },
        "reservation",
    )
    key = _identifier(row["reservation_key"], "reservation.reservation_key", 160)
    owner_id = _identifier(row["owner_id"], "reservation.owner_id", 96)
    state = _text(row["state"], "reservation.state", 16).upper()
    if state not in _RESERVATION_STATES:
        raise VendorPatchPursuitInputError("reservation.state is unknown")
    collision_count = _int(
        row["collision_count"], "reservation.collision_count", maximum=100000
    )
    observed_at, observed = _time(
        row["observed_at"], "reservation.observed_at"
    )
    expires_at, expires = _time(row["expires_at"], "reservation.expires_at")
    if observed > now:
        raise VendorPatchPursuitInputError("reservation observation is from the future")
    if expires <= observed:
        raise VendorPatchPursuitInputError(
            "reservation.expires_at must be after observed_at"
        )
    if expires - observed > _MAX_RESERVATION_HORIZON_SECONDS:
        raise VendorPatchPursuitInputError(
            "reservation horizon exceeds supported maximum"
        )
    normalized = {
        "reservation_key": key,
        "owner_id": owner_id,
        "state": state,
        "collision_count": collision_count,
        "observed_at": observed_at,
        "expires_at": expires_at,
        "evidence_ref": _url(row["evidence_ref"], "reservation.evidence_ref"),
        "evidence_sha256": _digest(
            row["evidence_sha256"], "reservation.evidence_sha256"
        ),
    }
    reasons: list[str] = []
    if owner_id != operator_id:
        reasons.append("RESERVATION_OWNER_MISMATCH")
    if state != "ACTIVE":
        reasons.append("RESERVATION_NOT_ACTIVE")
    if collision_count:
        reasons.append("RESERVATION_COLLISION")
    if now - observed > _RESERVATION_OBSERVATION_TTL_SECONDS:
        reasons.append("RESERVATION_OBSERVATION_STALE")
    if expires <= now:
        reasons.append("RESERVATION_EXPIRED")
    return normalized, reasons


def normalize_document(
    raw: Any, *, now_utc: str | None = None
) -> tuple[dict[str, Any], list[str], str]:
    doc = _obj(raw, "document")
    _keys(
        doc,
        {"schema", "opportunity_id", "operator_id", "candidate", "program", "reservation"},
        "document",
    )
    if doc["schema"] != SCHEMA_VERSION:
        raise VendorPatchPursuitInputError(
            f"document.schema must equal {SCHEMA_VERSION}"
        )
    opportunity_id = _identifier(doc["opportunity_id"], "opportunity_id", 120)
    operator_id = _identifier(doc["operator_id"], "operator_id", 96)
    now_text = trusted_utc_now() if now_utc is None else now_utc
    evaluated_at, now = _time(now_text, "evaluated_at")
    candidate, candidate_reasons = _normalize_candidate(doc["candidate"], now)
    program, program_reasons = _normalize_program(
        doc["program"], candidate["repository"], now
    )
    reservation, reservation_reasons = _normalize_reservation(
        doc["reservation"], operator_id, now
    )
    expected_key = (
        f"vendor-patch:{candidate['repository']}#{candidate['issue_number']}"
    )
    cross_reasons: list[str] = []
    if reservation["reservation_key"] != expected_key:
        cross_reasons.append("RESERVATION_KEY_MISMATCH")
    reasons = sorted(
        set(candidate_reasons + program_reasons + reservation_reasons + cross_reasons)
    )
    normalized = {
        "schema": SCHEMA_VERSION,
        "opportunity_id": opportunity_id,
        "operator_id": operator_id,
        "candidate": candidate,
        "program": program,
        "reservation": reservation,
    }
    return normalized, reasons, evaluated_at


def _packet(
    normalized: dict[str, Any], reasons: list[str], evaluated_at: str
) -> dict[str, Any]:
    candidate = normalized["candidate"]
    program = normalized["program"]
    reservation = normalized["reservation"]
    disposition = "PURSUE_FOR_OWNER_REVIEW" if not reasons else "HOLD"
    reward = {
        "state": program["reward_state"],
        "currency": program["currency"],
        "min_reward_minor": program["min_reward_minor"],
        "max_reward_minor": program["max_reward_minor"],
    }
    return {
        "schema": PACKET_VERSION,
        "opportunity_id": normalized["opportunity_id"],
        "operator_id": normalized["operator_id"],
        "evaluated_at": evaluated_at,
        "disposition": disposition,
        "reason_codes": reasons,
        "candidate": {
            "repository": candidate["repository"],
            "issue_number": candidate["issue_number"],
            "issue_url": candidate["issue_url"],
            "issue_evidence_ref": candidate["evidence_ref"],
            "issue_evidence_sha256": candidate["evidence_sha256"],
        },
        "program": {
            "program_id": program["program_id"],
            "program_name": program["program_name"],
            "source_url": program["source_url"],
            "program_evidence_ref": program["evidence_ref"],
            "program_evidence_sha256": program["evidence_sha256"],
            "reward": reward,
            "contribution_route": program["contribution_route"],
        },
        "reservation": {
            "reservation_key": reservation["reservation_key"],
            "owner_id": reservation["owner_id"],
            "expires_at": reservation["expires_at"],
            "reservation_evidence_ref": reservation["evidence_ref"],
            "reservation_evidence_sha256": reservation["evidence_sha256"],
        },
        "payoff_path_handoff": {
            "mechanism": "BOUNTY",
            "source_url": program["source_url"],
            "source_evidence_sha256": program["evidence_sha256"],
            "next_conversion_event": "SUBMIT_WORK",
            "reward": reward,
        },
        "authority_ceiling": {
            "owner_review_only": True,
            "authorizes_outreach": False,
            "authorizes_submission": False,
            "authorizes_terms_acceptance": False,
            "authorizes_spend": False,
            "asserts_award_or_payment": False,
            "asserts_revenue": False,
        },
    }


def _markdown(packet: dict[str, Any]) -> str:
    candidate = packet["candidate"]
    program = packet["program"]
    reward = program["reward"]
    if reward["state"] == "PROGRAM_SCHEDULE":
        reward_text = (
            f"{reward['currency']} {reward['min_reward_minor']}.."
            f"{reward['max_reward_minor']} minor units (program schedule only)"
        )
    else:
        reward_text = "not evidenced by source"
    reasons = (
        ", ".join(packet["reason_codes"]) if packet["reason_codes"] else "none"
    )
    return "\n".join(
        [
            "# Vendor Patch Pursuit Review",
            "",
            f"- Disposition: **{packet['disposition']}**",
            f"- Opportunity: `{packet['opportunity_id']}`",
            f"- Operator: `{packet['operator_id']}`",
            f"- Evaluated at: `{packet['evaluated_at']}`",
            f"- Candidate: `{candidate['repository']}#{candidate['issue_number']}`",
            f"- Issue: {candidate['issue_url']}",
            f"- Program: **{program['program_name']}** (`{program['program_id']}`)",
            f"- Program source: {program['source_url']}",
            f"- Program reward evidence: {reward_text}",
            f"- Contribution route: `{program['contribution_route']}`",
            f"- Reservation: `{packet['reservation']['reservation_key']}` "
            f"owned by `{packet['reservation']['owner_id']}` until "
            f"`{packet['reservation']['expires_at']}`",
            f"- Reason codes: {reasons}",
            "",
            "## Authority ceiling",
            "",
            "This packet is owner-review decision support only. It does not authorize "
            "outreach, submission, terms acceptance, spend, or external mutation, and "
            "it does not assert an award, payment, cash receipt, or recognized revenue.",
            "",
        ]
    )


def compile_pursuit(
    raw: Any, *, now_utc: str | None = None
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    normalized, reasons, evaluated_at = normalize_document(raw, now_utc=now_utc)
    packet = _packet(normalized, reasons, evaluated_at)
    markdown = _markdown(packet)
    receipt = {
        "schema": RECEIPT_VERSION,
        "evaluated_at": evaluated_at,
        "input_sha256": _sha_json(normalized),
        "packet_sha256": _sha_json(packet),
        "markdown_sha256": _sha_text(markdown),
        "disposition": packet["disposition"],
    }
    return packet, markdown, receipt


def verify_pursuit(
    raw: Any,
    packet: Any,
    markdown: Any,
    receipt: Any,
    *,
    now_utc: str | None = None,
) -> dict[str, Any]:
    receipt_obj = _obj(receipt, "receipt")
    _keys(
        receipt_obj,
        {
            "schema",
            "evaluated_at",
            "input_sha256",
            "packet_sha256",
            "markdown_sha256",
            "disposition",
        },
        "receipt",
    )
    if receipt_obj["schema"] != RECEIPT_VERSION:
        raise VendorPatchPursuitVerificationError("receipt schema mismatch")
    evaluated_at, evaluated = _time(
        receipt_obj["evaluated_at"], "receipt.evaluated_at"
    )
    verifier_time = trusted_utc_now() if now_utc is None else now_utc
    verifier_at, verifier = _time(verifier_time, "verifier_time")
    if verifier < evaluated:
        raise VendorPatchPursuitVerificationError(
            "verifier time predates receipt evaluation"
        )
    expected_packet, expected_markdown, expected_receipt = compile_pursuit(
        raw, now_utc=evaluated_at
    )
    if packet != expected_packet:
        raise VendorPatchPursuitVerificationError("packet does not match input")
    if type(markdown) is not str or markdown != expected_markdown:
        raise VendorPatchPursuitVerificationError("markdown does not match input")
    if receipt_obj != expected_receipt:
        raise VendorPatchPursuitVerificationError("receipt digest mismatch")
    current_packet, _, _ = compile_pursuit(raw, now_utc=verifier_at)
    if (
        expected_packet["disposition"] == "PURSUE_FOR_OWNER_REVIEW"
        and current_packet["disposition"] != "PURSUE_FOR_OWNER_REVIEW"
    ):
        raise VendorPatchPursuitVerificationError(
            "previous PURSUE receipt is no longer current"
        )
    return current_packet


def _load_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise VendorPatchPursuitInputError(
            f"{path} is not valid JSON"
        ) from exc


def _write_exclusive(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def _compile_command(args: argparse.Namespace) -> int:
    raw = _load_json(Path(args.input))
    packet, markdown, receipt = compile_pursuit(raw)
    _write_exclusive(
        Path(args.packet), json.dumps(packet, indent=2, sort_keys=True) + "\n"
    )
    _write_exclusive(Path(args.markdown), markdown)
    _write_exclusive(
        Path(args.receipt), json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    )
    print(packet["disposition"])
    return 0


def _verify_command(args: argparse.Namespace) -> int:
    raw = _load_json(Path(args.input))
    packet = _load_json(Path(args.packet))
    markdown = Path(args.markdown).read_text(encoding="utf-8")
    receipt = _load_json(Path(args.receipt))
    current = verify_pursuit(raw, packet, markdown, receipt)
    print(current["disposition"])
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile or verify vendor patch-reward pursuit evidence."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    compile_cmd = sub.add_parser("compile")
    verify_cmd = sub.add_parser("verify")
    for cmd in (compile_cmd, verify_cmd):
        cmd.add_argument("--input", required=True)
        cmd.add_argument("--packet", required=True)
        cmd.add_argument("--markdown", required=True)
        cmd.add_argument("--receipt", required=True)
    compile_cmd.set_defaults(func=_compile_command)
    verify_cmd.set_defaults(func=_verify_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
