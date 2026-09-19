#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Second-stage identity collision gate for swarm custody reports.

Consumes a deterministic report from ``tools/swarm_custody.py`` plus one
namespace binding for every TAKE event.  It does not replace the custody
ledger: it adds cross-work checks that the v1 ledger cannot express:

* one owner label concurrently used by multiple swarm sessions;
* one session id concurrently presenting multiple owner labels; and
* one claim id reused for different canonical GitHub issues.

This is advisory-only.  It performs no network calls or mutations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "swarm-custody-identity-gate/v1"
CUSTODY_SCHEMA_VERSION = 1
_BINDING_FIELDS = frozenset({"take_event_id", "session_id", "claim_id", "canonical_issue"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}$")
_ISSUE_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#([1-9][0-9]*)$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class IdentityGateError(ValueError):
    """Stable malformed-input error for the identity gate."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise IdentityGateError("noncanonical_json", "value is not canonical JSON") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _obj(value: Any, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise IdentityGateError("invalid_object", f"{field} must be an object")
    return value


def _identifier(value: Any, field: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise IdentityGateError("invalid_identifier", f"{field} is not a valid identifier")
    return value


def _canonical_issue(value: Any, field: str) -> str:
    if type(value) is not str:
        raise IdentityGateError("invalid_issue", f"{field} must be a string")
    match = _ISSUE_RE.fullmatch(value)
    if match is None:
        raise IdentityGateError(
            "invalid_issue", f"{field} must be canonical owner/repo#number"
        )
    owner, repo, number = match.groups()
    return f"{owner.casefold()}/{repo.casefold()}#{int(number)}"


def _validate_custody_report(value: Any) -> dict[str, Any]:
    """Validate the structural/integrity envelope emitted by swarm_custody v1.

    The SHA is an integrity checksum, not authentication.  This gate therefore
    labels it only as a custody anchor and never elevates it to external authority.
    """
    report = _obj(value, "custody_report")
    if report.get("schema_version") != CUSTODY_SCHEMA_VERSION:
        raise IdentityGateError(
            "unsupported_custody_schema",
            f"custody_report.schema_version must equal {CUSTODY_SCHEMA_VERSION}",
        )
    digest = report.get("receipt_sha256")
    if type(digest) is not str or _SHA256_RE.fullmatch(digest) is None:
        raise IdentityGateError(
            "invalid_custody_receipt", "custody_report receipt_sha256 is invalid"
        )
    body = dict(report)
    body.pop("receipt_sha256", None)
    if _digest(body) != digest:
        raise IdentityGateError(
            "custody_receipt_mismatch", "custody_report receipt_sha256 does not match"
        )
    events = report.get("events")
    items = report.get("items")
    if type(events) is not list or type(items) is not list:
        raise IdentityGateError(
            "invalid_custody_shape", "custody_report events/items must be arrays"
        )
    event_count = report.get("event_count")
    work_count = report.get("work_count")
    if type(event_count) is not int or event_count != len(events):
        raise IdentityGateError(
            "invalid_custody_shape", "custody_report event_count does not match events"
        )
    if type(work_count) is not int or work_count != len(items):
        raise IdentityGateError(
            "invalid_custody_shape", "custody_report work_count does not match items"
        )
    seen_events: set[str] = set()
    take_ids: set[str] = set()
    for index, raw in enumerate(events):
        event = _obj(raw, f"custody_report.events[{index}]")
        event_id = _identifier(event.get("event_id"), f"events[{index}].event_id")
        if event_id in seen_events:
            raise IdentityGateError("duplicate_event_id", f"duplicate event_id {event_id}")
        seen_events.add(event_id)
        if event.get("kind") == "TAKE":
            take_ids.add(event_id)
    for i, raw_item in enumerate(items):
        item = _obj(raw_item, f"custody_report.items[{i}]")
        _identifier(item.get("work_key"), f"items[{i}].work_key")
        active = item.get("active_claims")
        if type(active) is not list:
            raise IdentityGateError(
                "invalid_custody_shape", f"items[{i}].active_claims must be an array"
            )
        for j, raw_claim in enumerate(active):
            claim = _obj(raw_claim, f"items[{i}].active_claims[{j}]")
            take_event_id = _identifier(
                claim.get("take_event_id"), f"active_claims[{j}].take_event_id"
            )
            _identifier(claim.get("owner"), f"active_claims[{j}].owner")
            if take_event_id not in take_ids:
                raise IdentityGateError(
                    "active_take_missing",
                    f"active claim references non-TAKE event {take_event_id}",
                )
    return report


def _normalize_bindings(value: Any, take_ids: set[str]) -> list[dict[str, str]]:
    if type(value) is not list:
        raise IdentityGateError("invalid_bindings", "bindings must be an array")
    by_take: dict[str, dict[str, str]] = {}
    for index, raw in enumerate(value):
        binding = _obj(raw, f"bindings[{index}]")
        if set(binding) != _BINDING_FIELDS:
            raise IdentityGateError(
                "invalid_binding_fields",
                f"bindings[{index}] must contain exactly {sorted(_BINDING_FIELDS)}",
            )
        take_event_id = _identifier(binding["take_event_id"], f"bindings[{index}].take_event_id")
        if take_event_id in by_take:
            raise IdentityGateError(
                "duplicate_take_binding", f"duplicate binding for {take_event_id}"
            )
        if take_event_id not in take_ids:
            raise IdentityGateError(
                "extra_take_binding", f"binding references unknown TAKE {take_event_id}"
            )
        by_take[take_event_id] = {
            "take_event_id": take_event_id,
            "session_id": _identifier(binding["session_id"], f"bindings[{index}].session_id"),
            "claim_id": _identifier(binding["claim_id"], f"bindings[{index}].claim_id"),
            "canonical_issue": _canonical_issue(
                binding["canonical_issue"], f"bindings[{index}].canonical_issue"
            ),
        }
    missing = sorted(take_ids - set(by_take))
    if missing:
        raise IdentityGateError(
            "missing_take_binding", f"missing bindings for TAKE events: {', '.join(missing)}"
        )
    return [by_take[key] for key in sorted(by_take)]


def compile_identity_gate(request: Mapping[str, Any]) -> dict[str, Any]:
    request = _obj(request, "request")
    if request.get("schema") != SCHEMA:
        raise IdentityGateError("invalid_schema", f"schema must equal {SCHEMA}")
    if set(request) != {"schema", "custody_report", "bindings"}:
        raise IdentityGateError(
            "unexpected_request_fields",
            "request must contain exactly schema, custody_report, bindings",
        )

    report = _validate_custody_report(request["custody_report"])
    take_events: dict[str, dict[str, Any]] = {}
    for raw in report["events"]:
        if raw.get("kind") == "TAKE":
            take_events[raw["event_id"]] = raw
    bindings = _normalize_bindings(request["bindings"], set(take_events))

    active_take_ids: set[str] = set()
    for item in report["items"]:
        for claim in item["active_claims"]:
            active_take_ids.add(claim["take_event_id"])

    owner_sessions: dict[str, set[str]] = {}
    owner_takes: dict[str, list[str]] = {}
    session_owners: dict[str, set[str]] = {}
    session_takes: dict[str, list[str]] = {}
    claim_issues: dict[str, set[str]] = {}
    claim_takes: dict[str, list[str]] = {}

    for binding in bindings:
        take_id = binding["take_event_id"]
        event = take_events[take_id]
        claim_issues.setdefault(binding["claim_id"], set()).add(binding["canonical_issue"])
        claim_takes.setdefault(binding["claim_id"], []).append(take_id)
        if take_id in active_take_ids:
            owner = event["owner"]
            session = binding["session_id"]
            owner_sessions.setdefault(owner, set()).add(session)
            owner_takes.setdefault(owner, []).append(take_id)
            session_owners.setdefault(session, set()).add(owner)
            session_takes.setdefault(session, []).append(take_id)

    owner_collisions = [
        {
            "owner": owner,
            "session_ids": sorted(sessions),
            "take_event_ids": sorted(owner_takes[owner]),
        }
        for owner, sessions in sorted(owner_sessions.items())
        if len(sessions) > 1
    ]
    session_collisions = [
        {
            "session_id": session,
            "owners": sorted(owners),
            "take_event_ids": sorted(session_takes[session]),
        }
        for session, owners in sorted(session_owners.items())
        if len(owners) > 1
    ]
    claim_collisions = [
        {
            "claim_id": claim_id,
            "canonical_issues": sorted(issues),
            "take_event_ids": sorted(claim_takes[claim_id]),
        }
        for claim_id, issues in sorted(claim_issues.items())
        if len(issues) > 1
    ]

    reason_codes: list[str] = []
    if owner_collisions:
        reason_codes.append("OWNER_LABEL_SESSION_COLLISION")
    if session_collisions:
        reason_codes.append("SESSION_OWNER_LABEL_COLLISION")
    if claim_collisions:
        reason_codes.append("CLAIM_ID_ISSUE_COLLISION")

    body = {
        "schema": SCHEMA,
        "disposition": "HOLD_COLLISION" if reason_codes else "CLEAR",
        "reason_codes": reason_codes,
        "custody_anchor": {
            "schema_version": report["schema_version"],
            "as_of": report.get("as_of"),
            "event_count": report["event_count"],
            "work_count": report["work_count"],
            "receipt_sha256": report["receipt_sha256"],
            "receipt_is_integrity_only": True,
        },
        "bindings": bindings,
        "collisions": {
            "owner_labels": owner_collisions,
            "session_ids": session_collisions,
            "claim_ids": claim_collisions,
        },
        "authority": {
            "advisory_only": True,
            "provider_application_authority": False,
            "implementation_write_authority": False,
            "slack_mutation_authority": False,
            "github_mutation_authority": False,
            "payment_or_wallet_authority": False,
            "custody_receipt_is_authentication": False,
        },
    }
    return {**body, "receipt_sha256": _digest(body)}


def verify_identity_gate(receipt: Any) -> bool:
    if type(receipt) is not dict or receipt.get("schema") != SCHEMA:
        return False
    digest = receipt.get("receipt_sha256")
    if type(digest) is not str or _SHA256_RE.fullmatch(digest) is None:
        return False
    authority = receipt.get("authority")
    if authority != {
        "advisory_only": True,
        "provider_application_authority": False,
        "implementation_write_authority": False,
        "slack_mutation_authority": False,
        "github_mutation_authority": False,
        "payment_or_wallet_authority": False,
        "custody_receipt_is_authentication": False,
    }:
        return False
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    return _digest(body) == digest


def _load(path: str) -> Any:
    try:
        if path == "-":
            return json.load(sys.stdin)
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise IdentityGateError("input_error", f"cannot load {path}") from exc


def _error(exc: IdentityGateError) -> str:
    return json.dumps(
        {"error": {"code": exc.code, "message": exc.message}},
        sort_keys=True,
        separators=(",", ":"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect cross-work swarm owner/session and claim-id collisions."
    )
    parser.add_argument("request", help="identity-gate request JSON path, or - for stdin")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = compile_identity_gate(_load(args.request))
    except IdentityGateError as exc:
        print(_error(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(receipt, sort_keys=True, indent=2, ensure_ascii=False)
        if args.pretty
        else _canonical_json(receipt).decode("utf-8")
    )
    return 3 if receipt["disposition"] == "HOLD_COLLISION" else 0


if __name__ == "__main__":
    raise SystemExit(main())
