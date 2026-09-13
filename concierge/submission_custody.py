# SPDX-License-Identifier: MIT
"""Evidence-bound custody for bounty submission and sponsor-response lifecycle.

This module is deliberately offline.  It records what a human/operator says was
submitted and what sponsor evidence was observed; it never sends a message,
comments on GitHub, opens a PR, requests payout, moves funds, or infers sponsor
acceptance/payment from silence.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

SCHEMA = "bounty-submission-custody/v1"
_ROUTE_CLASSES = {"github_comment", "github_pr", "email_fallback", "form", "other"}
_SPONSOR_STATUSES = {
    "acknowledged",
    "needs_changes",
    "accepted",
    "rejected",
    "withdrawn",
    "payment_pending",
    "paid_evidence_ready",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_GH_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_SECRET_KEY_RE = re.compile(
    r"(?:secret|password|passwd|token|api[_-]?key|private[_-]?key|access[_-]?key|authorization)",
    re.IGNORECASE,
)
_MAX_EVENTS = 10_000
_MAX_PACKET_PATHS = 512
_MAX_PACKET_TESTS = 256
_MAX_PACKET_ACCEPTANCE = 256


class SubmissionCustodyError(ValueError):
    """Raised when custody evidence is malformed, conflicting, or unauthorized."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> None:
    raise SubmissionCustodyError(code, message)


def _plain_dict(value: Any, code: str = "OBJECT_REQUIRED") -> dict[str, Any]:
    if type(value) is not dict:
        _fail(code)
    return value


def _exact_keys(value: Any, keys: set[str], code: str) -> dict[str, Any]:
    obj = _plain_dict(value, code)
    if set(obj) != keys:
        _fail(code)
    return obj


def _text(value: Any, field: str, *, min_len: int = 1, max_len: int = 512) -> str:
    if type(value) is not str:
        _fail("TEXT_REQUIRED", field)
    if value != value.strip() or len(value) < min_len or len(value) > max_len:
        _fail("TEXT_NOT_CANONICAL", field)
    if "\x00" in value or any(ord(ch) < 32 for ch in value):
        _fail("TEXT_CONTROL_CHARACTER", field)
    return value


def _id(value: Any, field: str) -> str:
    text = _text(value, field, min_len=3, max_len=128)
    if not _ID_RE.fullmatch(text):
        _fail("INVALID_ID", field)
    return text


def _sha256(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if allow_empty and value == "":
        return ""
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        _fail("INVALID_SHA256", field)
    return value


def _timestamp(value: Any, field: str) -> tuple[str, datetime]:
    if type(value) is not str or not value.endswith("Z"):
        _fail("INVALID_TIMESTAMP", field)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail("INVALID_TIMESTAMP", field)
    if parsed.tzinfo != timezone.utc:
        _fail("INVALID_TIMESTAMP", field)
    if parsed.microsecond:
        canonical = parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
    else:
        canonical = parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
    if canonical != value:
        _fail("NONCANONICAL_TIMESTAMP", field)
    return canonical, parsed


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _github_object_url(value: Any, marker: str, code: str) -> tuple[str, str, str, int]:
    text = _text(value, f"github_{marker}_url", max_len=1024)
    parsed = urlsplit(text)
    try:
        port = parsed.port
    except ValueError:
        _fail(code)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        _fail(code)
    parts = parsed.path.split("/")
    if len(parts) != 5 or parts[0] != "" or parts[3] != marker:
        _fail(code)
    owner, repo, number = parts[1], parts[2], parts[4]
    if not _GH_NAME_RE.fullmatch(owner) or not _GH_NAME_RE.fullmatch(repo):
        _fail(code)
    if owner in {".", ".."} or repo in {".", ".."}:
        _fail(code)
    if not number.isdigit() or int(number) <= 0 or str(int(number)) != number:
        _fail(code)
    canonical = f"https://github.com/{owner}/{repo}/{marker}/{number}"
    if canonical != text:
        _fail(code)
    return canonical, owner, repo, int(number)


def _github_issue_url(value: Any) -> str:
    return _github_object_url(value, "issues", "SOURCE_URL_INVALID")[0]


def _positive_decimal_text(value: Any) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > 128:
        _fail("PACKET_REWARD_INVALID")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        _fail("PACKET_REWARD_INVALID")
    if not parsed.is_finite() or parsed <= 0:
        _fail("PACKET_REWARD_INVALID")
    parts = parsed.as_tuple()
    if len(parts.digits) > 64 or abs(parts.exponent) > 64:
        _fail("PACKET_REWARD_INVALID")
    return value


def _packet_path(value: Any, field: str) -> str:
    text = _text(value, field, max_len=1024)
    if "\\" in text:
        _fail("PACKET_PATH_INVALID", field)
    path = PurePosixPath(text)
    if path.is_absolute() or text.startswith("./") or text.endswith("/"):
        _fail("PACKET_PATH_INVALID", field)
    if any(part in {"", ".", ".."} for part in path.parts) or path.as_posix() != text:
        _fail("PACKET_PATH_INVALID", field)
    return text


def _packet_paths(value: Any, field: str) -> list[str]:
    items = _bounded_list(value, field, _MAX_PACKET_PATHS)
    if not items:
        _fail("PACKET_PATHS_EMPTY", field)
    paths = [_packet_path(item, field) for item in items]
    if len(paths) != len(set(paths)):
        _fail("PACKET_PATH_DUPLICATE", field)
    return paths


def _packet_tests(value: Any) -> None:
    items = _bounded_list(value, "tests", _MAX_PACKET_TESTS)
    if not items:
        _fail("PACKET_TESTS_EMPTY")
    commands: set[str] = set()
    for item in items:
        row = _exact_keys(item, {"command", "outcome"}, "PACKET_TEST_SHAPE_MISMATCH")
        command = _text(row["command"], "packet.test.command", max_len=1024)
        if command in commands:
            _fail("PACKET_TEST_DUPLICATE")
        commands.add(command)
        if row["outcome"] != "PASS":
            _fail("PACKET_TEST_NOT_PASS")


def _packet_acceptance(value: Any) -> None:
    items = _bounded_list(value, "acceptance_checks", _MAX_PACKET_ACCEPTANCE)
    if not items:
        _fail("PACKET_ACCEPTANCE_EMPTY")
    ids: set[str] = set()
    for item in items:
        row = _exact_keys(item, {"criterion_id", "status"}, "PACKET_ACCEPTANCE_SHAPE_MISMATCH")
        criterion = _text(row["criterion_id"], "packet.acceptance.criterion_id", max_len=1024)
        if criterion in ids:
            _fail("PACKET_ACCEPTANCE_DUPLICATE")
        ids.add(criterion)
        if row["status"] != "PASS":
            _fail("PACKET_ACCEPTANCE_NOT_PASS")


def _reject_secret_keys(value: Any, path: str = "$") -> None:
    if type(value) is list:
        for index, item in enumerate(value):
            _reject_secret_keys(item, f"{path}[{index}]")
        return
    if type(value) is not dict:
        return
    for key, child in value.items():
        if type(key) is not str:
            _fail("NONSTRING_KEY", path)
        fingerprint = re.sub(r"[^A-Za-z0-9]", "", key)
        if _SECRET_KEY_RE.search(fingerprint):
            _fail("SECRET_SHAPED_FIELD", f"{path}.{key}")
        _reject_secret_keys(child, f"{path}.{key}")


def _bounded_list(value: Any, field: str, limit: int) -> list[Any]:
    if type(value) is not list or len(value) > limit:
        _fail("LIST_INVALID", field)
    return value


def _verify_submission_packet(packet: Any) -> dict[str, str]:
    _reject_secret_keys(packet)
    packet = _exact_keys(
        packet,
        {
            "canonical_source_url",
            "advertised_reward_usd",
            "disposition",
            "reason_codes",
            "evidence",
            "authority",
            "packet_sha256",
        },
        "PACKET_SHAPE_MISMATCH",
    )
    source, source_owner, source_repo, _ = _github_object_url(
        packet["canonical_source_url"], "issues", "SOURCE_URL_INVALID"
    )
    _positive_decimal_text(packet["advertised_reward_usd"])
    if packet["disposition"] != "READY_FOR_HUMAN_SUBMISSION":
        _fail("PACKET_NOT_READY")
    if packet["reason_codes"] != []:
        _fail("PACKET_REASON_MISMATCH")
    authority = _exact_keys(
        packet["authority"],
        {"submission", "reward", "acceptance", "payout", "cash_claim"},
        "PACKET_AUTHORITY_MISMATCH",
    )
    if authority != {
        "submission": "human_only",
        "reward": "advertised_only",
        "acceptance": "not_inferred",
        "payout": "not_inferred",
        "cash_claim": False,
    }:
        _fail("PACKET_AUTHORITY_MISMATCH")

    evidence = _exact_keys(
        packet["evidence"],
        {
            "pull_request_url",
            "pull_request_repo",
            "pull_request_number",
            "head_sha",
            "changed_paths",
            "allowed_paths",
            "tests",
            "evidence_sha256",
            "acceptance_checks",
        },
        "PACKET_EVIDENCE_SHAPE_MISMATCH",
    )
    _, pr_owner, pr_repo, pr_number = _github_object_url(
        evidence["pull_request_url"], "pull", "PACKET_PR_URL_INVALID"
    )
    expected_repo = f"{pr_owner}/{pr_repo}"
    if evidence["pull_request_repo"] != expected_repo:
        _fail("PACKET_PR_REPO_INVALID")
    if (source_owner.casefold(), source_repo.casefold()) != (pr_owner.casefold(), pr_repo.casefold()):
        _fail("PACKET_PR_SOURCE_REPO_MISMATCH")
    if isinstance(evidence["pull_request_number"], bool) or type(evidence["pull_request_number"]) is not int:
        _fail("PACKET_PR_NUMBER_INVALID")
    if evidence["pull_request_number"] != pr_number:
        _fail("PACKET_PR_NUMBER_INVALID")
    if type(evidence["head_sha"]) is not str or not _SHA40_RE.fullmatch(evidence["head_sha"]):
        _fail("PACKET_HEAD_SHA_INVALID")
    _sha256(evidence["evidence_sha256"], "packet.evidence.evidence_sha256")
    changed = _packet_paths(evidence["changed_paths"], "changed_paths")
    allowed = _packet_paths(evidence["allowed_paths"], "allowed_paths")
    if set(changed) - set(allowed):
        _fail("PACKET_CHANGED_PATH_OUTSIDE_ALLOWLIST")
    _packet_tests(evidence["tests"])
    _packet_acceptance(evidence["acceptance_checks"])

    packet_sha = _sha256(packet["packet_sha256"], "packet_sha256")
    core = {key: deepcopy(value) for key, value in packet.items() if key != "packet_sha256"}
    if _digest(core) != packet_sha:
        _fail("PACKET_DIGEST_MISMATCH")
    return {
        "source_url": source,
        "packet_sha256": packet_sha,
        "artifact_digest": evidence["evidence_sha256"],
        "head_sha": evidence["head_sha"],
    }


def strict_json_loads(text: str) -> Any:
    """Load JSON while rejecting duplicate object keys at every depth."""
    if type(text) is not str:
        _fail("JSON_TEXT_REQUIRED")

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail("DUPLICATE_JSON_KEY", key)
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=pairs_hook)
    except SubmissionCustodyError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SubmissionCustodyError("INVALID_JSON") from exc


def _event_digest(event: dict[str, Any]) -> str:
    return _digest(event)


def _ledger_digest(events: list[dict[str, Any]]) -> str:
    return _digest({"schema": SCHEMA, "events": events})


def empty_ledger() -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    return {"schema": SCHEMA, "events": events, "ledger_sha256": _ledger_digest(events)}


def _normalize_ledger(ledger: Any) -> dict[str, Any]:
    obj = _exact_keys(ledger, {"schema", "events", "ledger_sha256"}, "LEDGER_SHAPE_MISMATCH")
    if obj["schema"] != SCHEMA:
        _fail("LEDGER_SCHEMA_MISMATCH")
    events = _bounded_list(obj["events"], "events", _MAX_EVENTS)
    _sha256(obj["ledger_sha256"], "ledger_sha256")
    if _ledger_digest(events) != obj["ledger_sha256"]:
        _fail("LEDGER_DIGEST_MISMATCH")
    return deepcopy(obj)


def _assert_as_of(as_of: Any) -> tuple[str, datetime]:
    return _timestamp(as_of, "as_of")


def _route_key(value: Any) -> str:
    key = _id(value, "route_key")
    if "@" in key or "/" in key or "\\" in key:
        _fail("ROUTE_KEY_NOT_OPAQUE")
    return key


def _event_id(value: Any) -> str:
    return _id(value, "event_id")


def _submission_id(value: Any) -> str:
    return _id(value, "submission_id")


def _external_key(value: Any, field: str) -> str:
    key = _id(value, field)
    if "@" in key or "/" in key or "\\" in key:
        _fail("EXTERNAL_KEY_NOT_OPAQUE", field)
    return key


def _ensure_not_future(occurred: datetime, as_of: datetime) -> None:
    if occurred > as_of:
        _fail("FUTURE_EVENT")


def _make_register_event(
    *,
    event_id: Any,
    occurred_at: Any,
    submission_id: Any,
    packet: Any,
    artifact_revision: Any,
    route_class: Any,
    route_key: Any,
    contract_digest: Any,
    as_of: datetime,
) -> dict[str, Any]:
    packet_binding = _verify_submission_packet(packet)
    event_time, event_dt = _timestamp(occurred_at, "occurred_at")
    _ensure_not_future(event_dt, as_of)
    if isinstance(artifact_revision, bool) or type(artifact_revision) is not int or artifact_revision <= 0:
        _fail("ARTIFACT_REVISION_INVALID")
    if route_class not in _ROUTE_CLASSES:
        _fail("ROUTE_CLASS_INVALID")
    return {
        "event_id": _event_id(event_id),
        "event_type": "candidate_registered",
        "occurred_at": event_time,
        "submission_id": _submission_id(submission_id),
        "source_url": packet_binding["source_url"],
        "packet_sha256": packet_binding["packet_sha256"],
        "artifact_digest": packet_binding["artifact_digest"],
        "artifact_revision": artifact_revision,
        "head_sha": packet_binding["head_sha"],
        "route_class": route_class,
        "route_key": _route_key(route_key),
        "contract_digest": _sha256(contract_digest, "contract_digest", allow_empty=True),
    }


def _make_dispatch_event(
    *,
    event_id: Any,
    event_type: str,
    occurred_at: Any,
    submission_id: Any,
    submission_digest: Any,
    route_class: Any,
    provider_receipt_key: Any,
    evidence_digest: Any,
    as_of: datetime,
) -> dict[str, Any]:
    event_time, event_dt = _timestamp(occurred_at, "occurred_at")
    _ensure_not_future(event_dt, as_of)
    if route_class not in _ROUTE_CLASSES:
        _fail("ROUTE_CLASS_INVALID")
    return {
        "event_id": _event_id(event_id),
        "event_type": event_type,
        "occurred_at": event_time,
        "submission_id": _submission_id(submission_id),
        "submission_digest": _sha256(submission_digest, "submission_digest"),
        "route_class": route_class,
        "provider_receipt_key": _external_key(provider_receipt_key, "provider_receipt_key"),
        "evidence_digest": _sha256(evidence_digest, "evidence_digest"),
    }


def _make_sponsor_event(
    *,
    event_id: Any,
    occurred_at: Any,
    submission_id: Any,
    submission_digest: Any,
    status: Any,
    provider_event_key: Any,
    evidence_digest: Any,
    as_of: datetime,
) -> dict[str, Any]:
    event_time, event_dt = _timestamp(occurred_at, "occurred_at")
    _ensure_not_future(event_dt, as_of)
    if status not in _SPONSOR_STATUSES:
        _fail("SPONSOR_STATUS_INVALID")
    return {
        "event_id": _event_id(event_id),
        "event_type": "sponsor_event",
        "occurred_at": event_time,
        "submission_id": _submission_id(submission_id),
        "submission_digest": _sha256(submission_digest, "submission_digest"),
        "status": status,
        "provider_event_key": _external_key(provider_event_key, "provider_event_key"),
        "evidence_digest": _sha256(evidence_digest, "evidence_digest"),
    }


def _make_supersede_event(
    *,
    event_id: Any,
    occurred_at: Any,
    old_submission_id: Any,
    old_submission_digest: Any,
    new_submission_id: Any,
    new_submission_digest: Any,
    evidence_digest: Any,
    as_of: datetime,
) -> dict[str, Any]:
    event_time, event_dt = _timestamp(occurred_at, "occurred_at")
    _ensure_not_future(event_dt, as_of)
    return {
        "event_id": _event_id(event_id),
        "event_type": "candidate_superseded",
        "occurred_at": event_time,
        "old_submission_id": _submission_id(old_submission_id),
        "old_submission_digest": _sha256(old_submission_digest, "old_submission_digest"),
        "new_submission_id": _submission_id(new_submission_id),
        "new_submission_digest": _sha256(new_submission_digest, "new_submission_digest"),
        "evidence_digest": _sha256(evidence_digest, "evidence_digest"),
    }


_REGISTER_KEYS = {
    "event_id", "event_type", "occurred_at", "submission_id", "source_url", "packet_sha256",
    "artifact_digest", "artifact_revision", "head_sha", "route_class", "route_key", "contract_digest",
}
_DISPATCH_KEYS = {
    "event_id", "event_type", "occurred_at", "submission_id", "submission_digest", "route_class",
    "provider_receipt_key", "evidence_digest",
}
_SPONSOR_KEYS = {
    "event_id", "event_type", "occurred_at", "submission_id", "submission_digest", "status",
    "provider_event_key", "evidence_digest",
}
_SUPERSEDE_KEYS = {
    "event_id", "event_type", "occurred_at", "old_submission_id", "old_submission_digest",
    "new_submission_id", "new_submission_digest", "evidence_digest",
}


def _validate_stored_event(event: Any, as_of: datetime) -> dict[str, Any]:
    obj = _plain_dict(event, "EVENT_OBJECT_REQUIRED")
    event_type = obj.get("event_type")
    if event_type == "candidate_registered":
        _exact_keys(obj, _REGISTER_KEYS, "REGISTER_EVENT_SHAPE_MISMATCH")
        normalized = {
            "event_id": _event_id(obj["event_id"]),
            "event_type": "candidate_registered",
            "occurred_at": _timestamp(obj["occurred_at"], "occurred_at")[0],
            "submission_id": _submission_id(obj["submission_id"]),
            "source_url": _github_issue_url(obj["source_url"]),
            "packet_sha256": _sha256(obj["packet_sha256"], "packet_sha256"),
            "artifact_digest": _sha256(obj["artifact_digest"], "artifact_digest"),
            "artifact_revision": obj["artifact_revision"],
            "head_sha": obj["head_sha"],
            "route_class": obj["route_class"],
            "route_key": _route_key(obj["route_key"]),
            "contract_digest": _sha256(obj["contract_digest"], "contract_digest", allow_empty=True),
        }
        if isinstance(normalized["artifact_revision"], bool) or type(normalized["artifact_revision"]) is not int or normalized["artifact_revision"] <= 0:
            _fail("ARTIFACT_REVISION_INVALID")
        if type(normalized["head_sha"]) is not str or not _SHA40_RE.fullmatch(normalized["head_sha"]):
            _fail("PACKET_HEAD_SHA_INVALID")
        if normalized["route_class"] not in _ROUTE_CLASSES:
            _fail("ROUTE_CLASS_INVALID")
    elif event_type in {"dispatched", "follow_up_recorded"}:
        _exact_keys(obj, _DISPATCH_KEYS, "DISPATCH_EVENT_SHAPE_MISMATCH")
        normalized = _make_dispatch_event(
            event_id=obj["event_id"], event_type=event_type, occurred_at=obj["occurred_at"],
            submission_id=obj["submission_id"], submission_digest=obj["submission_digest"],
            route_class=obj["route_class"], provider_receipt_key=obj["provider_receipt_key"],
            evidence_digest=obj["evidence_digest"], as_of=as_of,
        )
    elif event_type == "sponsor_event":
        _exact_keys(obj, _SPONSOR_KEYS, "SPONSOR_EVENT_SHAPE_MISMATCH")
        normalized = _make_sponsor_event(
            event_id=obj["event_id"], occurred_at=obj["occurred_at"], submission_id=obj["submission_id"],
            submission_digest=obj["submission_digest"], status=obj["status"],
            provider_event_key=obj["provider_event_key"], evidence_digest=obj["evidence_digest"], as_of=as_of,
        )
    elif event_type == "candidate_superseded":
        _exact_keys(obj, _SUPERSEDE_KEYS, "SUPERSEDE_EVENT_SHAPE_MISMATCH")
        normalized = _make_supersede_event(
            event_id=obj["event_id"], occurred_at=obj["occurred_at"],
            old_submission_id=obj["old_submission_id"], old_submission_digest=obj["old_submission_digest"],
            new_submission_id=obj["new_submission_id"], new_submission_digest=obj["new_submission_digest"],
            evidence_digest=obj["evidence_digest"], as_of=as_of,
        )
    else:
        _fail("EVENT_TYPE_INVALID")
    event_dt = _timestamp(normalized["occurred_at"], "occurred_at")[1]
    _ensure_not_future(event_dt, as_of)
    return normalized


def _replay(events: list[dict[str, Any]], as_of: datetime) -> dict[str, Any]:
    candidates: dict[str, dict[str, Any]] = {}
    event_ids: dict[str, str] = {}
    external_refs: dict[str, str] = {}
    identity_keys: dict[tuple[str, int], str] = {}
    superseded: set[str] = set()
    dispatches: dict[str, list[dict[str, Any]]] = {}
    sponsor_events: dict[str, list[dict[str, Any]]] = {}
    timeline: dict[str, datetime] = {}
    normalized_events: list[dict[str, Any]] = []

    for raw in events:
        event = _validate_stored_event(raw, as_of)
        event_digest = _event_digest(event)
        prior_digest = event_ids.get(event["event_id"])
        if prior_digest is not None:
            if prior_digest != event_digest:
                _fail("EVENT_ID_CONFLICT")
            _fail("DUPLICATE_EVENT_IN_LEDGER")
        event_ids[event["event_id"]] = event_digest
        event_dt = _timestamp(event["occurred_at"], "occurred_at")[1]

        if event["event_type"] == "candidate_registered":
            sid = event["submission_id"]
            if sid in candidates:
                _fail("SUBMISSION_ID_CONFLICT")
            identity = (event["source_url"], event["artifact_revision"])
            if identity in identity_keys:
                _fail("SOURCE_REVISION_CONFLICT")
            identity_keys[identity] = sid
            candidate_core = {
                key: value for key, value in event.items() if key not in {"event_id", "event_type", "occurred_at"}
            }
            candidate_digest = _digest(candidate_core)
            candidates[sid] = {**event, "submission_digest": candidate_digest}
            timeline[sid] = event_dt
            dispatches[sid] = []
            sponsor_events[sid] = []

        elif event["event_type"] in {"dispatched", "follow_up_recorded"}:
            sid = event["submission_id"]
            candidate = candidates.get(sid)
            if candidate is None:
                _fail("UNKNOWN_SUBMISSION")
            if event["submission_digest"] != candidate["submission_digest"]:
                _fail("SUBMISSION_DIGEST_MISMATCH")
            if sid in superseded:
                _fail("SUPERSEDED_SUBMISSION_MUTATION")
            if event_dt < timeline[sid]:
                _fail("TIMESTAMP_INVERSION")
            if event["route_class"] != candidate["route_class"]:
                _fail("ROUTE_CLASS_MISMATCH")
            ext = event["provider_receipt_key"]
            if ext in external_refs:
                _fail("EXTERNAL_REFERENCE_REUSE")
            external_refs[ext] = event["event_id"]
            if event["event_type"] == "dispatched" and any(
                item["event_type"] == "dispatched" for item in dispatches[sid]
            ):
                _fail("MULTIPLE_PRIMARY_DISPATCH")
            if event["event_type"] == "follow_up_recorded" and not any(
                item["event_type"] == "dispatched" for item in dispatches[sid]
            ):
                _fail("FOLLOW_UP_BEFORE_DISPATCH")
            if sponsor_events[sid] and sponsor_events[sid][-1]["status"] in {
                "accepted", "payment_pending", "paid_evidence_ready", "rejected", "withdrawn"
            }:
                _fail("FOLLOW_UP_AFTER_TERMINAL_RESPONSE")
            dispatches[sid].append(event)
            timeline[sid] = event_dt

        elif event["event_type"] == "sponsor_event":
            sid = event["submission_id"]
            candidate = candidates.get(sid)
            if candidate is None:
                _fail("UNKNOWN_SUBMISSION")
            if event["submission_digest"] != candidate["submission_digest"]:
                _fail("SUBMISSION_DIGEST_MISMATCH")
            if sid in superseded:
                _fail("SUPERSEDED_SUBMISSION_MUTATION")
            if not any(item["event_type"] == "dispatched" for item in dispatches[sid]):
                _fail("SPONSOR_EVENT_BEFORE_DISPATCH")
            if event_dt < timeline[sid]:
                _fail("TIMESTAMP_INVERSION")
            ext = event["provider_event_key"]
            if ext in external_refs:
                _fail("EXTERNAL_REFERENCE_REUSE")
            external_refs[ext] = event["event_id"]
            previous = sponsor_events[sid][-1]["status"] if sponsor_events[sid] else None
            status = event["status"]
            if previous in {"paid_evidence_ready", "rejected", "withdrawn"}:
                _fail("TERMINAL_RESPONSE_REOPEN")
            if previous in {"accepted", "payment_pending"} and status not in {
                "payment_pending", "paid_evidence_ready", "withdrawn"
            }:
                _fail("ACCEPTED_RESPONSE_REGRESSION")
            if status == "payment_pending" and previous not in {"accepted", "payment_pending"}:
                _fail("PAYMENT_PENDING_WITHOUT_ACCEPTANCE")
            if status == "paid_evidence_ready" and previous not in {"accepted", "payment_pending"}:
                _fail("PAID_EVIDENCE_WITHOUT_ACCEPTANCE")
            sponsor_events[sid].append(event)
            timeline[sid] = event_dt

        else:  # candidate_superseded
            old_id = event["old_submission_id"]
            new_id = event["new_submission_id"]
            old = candidates.get(old_id)
            new = candidates.get(new_id)
            if old is None or new is None:
                _fail("SUPERSEDE_UNKNOWN_SUBMISSION")
            if old_id == new_id:
                _fail("SELF_SUPERSESSION")
            if old_id in superseded or new_id in superseded:
                _fail("SUPERSESSION_CHAIN_INVALID")
            if event["old_submission_digest"] != old["submission_digest"] or event["new_submission_digest"] != new["submission_digest"]:
                _fail("SUPERSESSION_DIGEST_MISMATCH")
            if old["source_url"] != new["source_url"]:
                _fail("SUPERSESSION_SOURCE_MISMATCH")
            if new["artifact_revision"] <= old["artifact_revision"]:
                _fail("SUPERSESSION_REVISION_NOT_FORWARD")
            if event_dt < max(timeline[old_id], timeline[new_id]):
                _fail("TIMESTAMP_INVERSION")
            old_status = sponsor_events[old_id][-1]["status"] if sponsor_events[old_id] else None
            if old_status in {"accepted", "payment_pending", "paid_evidence_ready", "rejected", "withdrawn"}:
                _fail("SUPERSESSION_AFTER_TERMINAL_RESPONSE")
            superseded.add(old_id)
            timeline[old_id] = event_dt
            timeline[new_id] = max(timeline[new_id], event_dt)

        normalized_events.append(event)

    return {
        "events": normalized_events,
        "candidates": candidates,
        "dispatches": dispatches,
        "sponsor_events": sponsor_events,
        "superseded": superseded,
        "timeline": timeline,
    }


def verify_ledger(ledger: Any, *, as_of: Any) -> dict[str, Any]:
    _, as_of_dt = _assert_as_of(as_of)
    normalized = _normalize_ledger(ledger)
    replay = _replay(normalized["events"], as_of_dt)
    if replay["events"] != normalized["events"]:
        _fail("EVENT_NORMALIZATION_DRIFT")
    return {
        "valid": True,
        "event_count": len(replay["events"]),
        "submission_count": len(replay["candidates"]),
        "ledger_sha256": normalized["ledger_sha256"],
    }


def _append_event(ledger: Any, event: dict[str, Any], *, as_of: Any) -> tuple[dict[str, Any], bool]:
    _, as_of_dt = _assert_as_of(as_of)
    normalized = _normalize_ledger(ledger)
    replay = _replay(normalized["events"], as_of_dt)
    event_digest = _event_digest(event)
    for existing in replay["events"]:
        if existing["event_id"] == event["event_id"]:
            if _event_digest(existing) == event_digest:
                return normalized, True
            _fail("EVENT_ID_CONFLICT")
    next_events = replay["events"] + [event]
    _replay(next_events, as_of_dt)
    next_ledger = {"schema": SCHEMA, "events": next_events, "ledger_sha256": _ledger_digest(next_events)}
    return next_ledger, False


def register_candidate(
    ledger: Any,
    *,
    event_id: Any,
    occurred_at: Any,
    submission_id: Any,
    packet: Any,
    artifact_revision: Any,
    route_class: Any,
    route_key: Any,
    contract_digest: Any = "",
    as_of: Any,
) -> dict[str, Any]:
    _, as_of_dt = _assert_as_of(as_of)
    event = _make_register_event(
        event_id=event_id,
        occurred_at=occurred_at,
        submission_id=submission_id,
        packet=packet,
        artifact_revision=artifact_revision,
        route_class=route_class,
        route_key=route_key,
        contract_digest=contract_digest,
        as_of=as_of_dt,
    )
    next_ledger, replayed = _append_event(ledger, event, as_of=as_of)
    receipt = evaluate_submission(next_ledger, event["submission_id"], as_of=as_of, follow_up_after_seconds=86_400)
    return {"ledger": next_ledger, "replayed": replayed, "receipt": receipt}


def record_dispatch(
    ledger: Any,
    *,
    event_id: Any,
    occurred_at: Any,
    submission_id: Any,
    submission_digest: Any,
    route_class: Any,
    provider_receipt_key: Any,
    evidence_digest: Any,
    as_of: Any,
) -> dict[str, Any]:
    _, as_of_dt = _assert_as_of(as_of)
    event = _make_dispatch_event(
        event_id=event_id,
        event_type="dispatched",
        occurred_at=occurred_at,
        submission_id=submission_id,
        submission_digest=submission_digest,
        route_class=route_class,
        provider_receipt_key=provider_receipt_key,
        evidence_digest=evidence_digest,
        as_of=as_of_dt,
    )
    next_ledger, replayed = _append_event(ledger, event, as_of=as_of)
    return {"ledger": next_ledger, "replayed": replayed}


def record_follow_up(
    ledger: Any,
    *,
    event_id: Any,
    occurred_at: Any,
    submission_id: Any,
    submission_digest: Any,
    route_class: Any,
    provider_receipt_key: Any,
    evidence_digest: Any,
    as_of: Any,
) -> dict[str, Any]:
    _, as_of_dt = _assert_as_of(as_of)
    event = _make_dispatch_event(
        event_id=event_id,
        event_type="follow_up_recorded",
        occurred_at=occurred_at,
        submission_id=submission_id,
        submission_digest=submission_digest,
        route_class=route_class,
        provider_receipt_key=provider_receipt_key,
        evidence_digest=evidence_digest,
        as_of=as_of_dt,
    )
    next_ledger, replayed = _append_event(ledger, event, as_of=as_of)
    return {"ledger": next_ledger, "replayed": replayed}


def record_sponsor_event(
    ledger: Any,
    *,
    event_id: Any,
    occurred_at: Any,
    submission_id: Any,
    submission_digest: Any,
    status: Any,
    provider_event_key: Any,
    evidence_digest: Any,
    as_of: Any,
) -> dict[str, Any]:
    _, as_of_dt = _assert_as_of(as_of)
    event = _make_sponsor_event(
        event_id=event_id,
        occurred_at=occurred_at,
        submission_id=submission_id,
        submission_digest=submission_digest,
        status=status,
        provider_event_key=provider_event_key,
        evidence_digest=evidence_digest,
        as_of=as_of_dt,
    )
    next_ledger, replayed = _append_event(ledger, event, as_of=as_of)
    return {"ledger": next_ledger, "replayed": replayed}


def supersede_candidate(
    ledger: Any,
    *,
    event_id: Any,
    occurred_at: Any,
    old_submission_id: Any,
    old_submission_digest: Any,
    new_submission_id: Any,
    new_submission_digest: Any,
    evidence_digest: Any,
    as_of: Any,
) -> dict[str, Any]:
    _, as_of_dt = _assert_as_of(as_of)
    event = _make_supersede_event(
        event_id=event_id,
        occurred_at=occurred_at,
        old_submission_id=old_submission_id,
        old_submission_digest=old_submission_digest,
        new_submission_id=new_submission_id,
        new_submission_digest=new_submission_digest,
        evidence_digest=evidence_digest,
        as_of=as_of_dt,
    )
    next_ledger, replayed = _append_event(ledger, event, as_of=as_of)
    return {"ledger": next_ledger, "replayed": replayed}


def _candidate_state(replay: dict[str, Any], sid: str, as_of: datetime, follow_up_after_seconds: int) -> tuple[str, str, datetime]:
    candidate = replay["candidates"][sid]
    if sid in replay["superseded"]:
        return "HOLD", "SUPERSEDED", replay["timeline"][sid]
    outbound = replay["dispatches"][sid]
    sponsor = replay["sponsor_events"][sid]
    primary = [event for event in outbound if event["event_type"] == "dispatched"]
    if not primary:
        return "READY_TO_SUBMIT", "READY_TO_SUBMIT", _timestamp(candidate["occurred_at"], "occurred_at")[1]

    send = "ALREADY_SUBMITTED"
    latest_status = sponsor[-1]["status"] if sponsor else None
    if latest_status == "paid_evidence_ready":
        return send, "SETTLEMENT_EVIDENCE_READY", replay["timeline"][sid]
    if latest_status in {"accepted", "payment_pending"}:
        return send, "ACCEPTED_AWAITING_SETTLEMENT", replay["timeline"][sid]
    if latest_status == "needs_changes":
        return send, "NEEDS_CHANGES", replay["timeline"][sid]
    if latest_status == "rejected":
        return "HOLD", "CLOSED_REJECTED", replay["timeline"][sid]
    if latest_status == "withdrawn":
        return "HOLD", "WITHDRAWN", replay["timeline"][sid]

    baseline = replay["timeline"][sid]
    elapsed = (as_of - baseline).total_seconds()
    if elapsed >= follow_up_after_seconds:
        return send, "FOLLOW_UP_DUE", baseline
    return send, "AWAITING_SPONSOR", baseline


def evaluate_submission(
    ledger: Any,
    submission_id: Any,
    *,
    as_of: Any,
    follow_up_after_seconds: Any,
) -> dict[str, Any]:
    as_of_text, as_of_dt = _assert_as_of(as_of)
    if isinstance(follow_up_after_seconds, bool) or type(follow_up_after_seconds) is not int:
        _fail("FOLLOW_UP_POLICY_INVALID")
    if not 3600 <= follow_up_after_seconds <= 90 * 24 * 3600:
        _fail("FOLLOW_UP_POLICY_INVALID")
    normalized = _normalize_ledger(ledger)
    replay = _replay(normalized["events"], as_of_dt)
    sid = _submission_id(submission_id)
    candidate = replay["candidates"].get(sid)
    if candidate is None:
        _fail("UNKNOWN_SUBMISSION")
    send, lifecycle, baseline = _candidate_state(replay, sid, as_of_dt, follow_up_after_seconds)
    sponsor = replay["sponsor_events"][sid]
    outbound = replay["dispatches"][sid]
    receipt_core = {
        "schema": SCHEMA,
        "as_of": as_of_text,
        "submission_id": sid,
        "submission_digest": candidate["submission_digest"],
        "source_url": candidate["source_url"],
        "packet_sha256": candidate["packet_sha256"],
        "artifact_digest": candidate["artifact_digest"],
        "artifact_revision": candidate["artifact_revision"],
        "send_disposition": send,
        "lifecycle_state": lifecycle,
        "follow_up_after_seconds": follow_up_after_seconds,
        "follow_up_baseline": baseline.isoformat(timespec="microseconds" if baseline.microsecond else "seconds").replace("+00:00", "Z"),
        "outbound_event_count": len(outbound),
        "sponsor_event_count": len(sponsor),
        "ledger_sha256": normalized["ledger_sha256"],
        "authority": {
            "external_send_performed": False,
            "sponsor_response_inferred": False,
            "acceptance_inferred": False,
            "payment_inferred": False,
            "payout_requested": False,
            "wallet_mutated": False,
            "revenue_recognized": False,
        },
    }
    return {**receipt_core, "receipt_sha256": _digest(receipt_core)}
