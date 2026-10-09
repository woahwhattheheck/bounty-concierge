# SPDX-License-Identifier: MIT
"""Advance already-validated MOVA bounty packets through evidenced role handoffs.

Offline only: receipts here are operator-supplied assertions, not provider readbacks.
No claim, assignment, payment, publication or external mutation occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

from concierge.mova_factory import BATCH_SCHEMA, SCHEMA

RECEIPT_SCHEMA = "mova-role-receipt/v1"
PROGRESS_SCHEMA = "mova-wave-progress/v2"
ROLE_SPEC = (
    ("SCOUT", "SCOUT_RECEIPT", "COMPLETE"),
    ("BUILD", "BUILD_RECEIPT", "COMPLETE"),
    ("QA", "QA_ACCEPT_RECEIPT", "ACCEPTED"),
    ("PUBLISH", "PUBLICATION_RECEIPT", "COMPLETE"),
    ("COLLECT", "COLLECT_RECEIPT", "COMPLETE"),
)
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")


class MovaProgressError(ValueError):
    """The manifest or claimed completion evidence cannot be advanced."""


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MovaProgressError(message)


def _text(value: Any, field: str, limit: int = 512) -> str:
    _require(type(value) is str and 0 < len(value.encode("utf-8")) <= limit
             and value == value.strip() and "\0" not in value, f"invalid {field}")
    return value


def _verify_digest(obj: dict[str, Any], field: str) -> None:
    claimed = _text(obj.get(field), field, 64)
    _require(SHA256.fullmatch(claimed) is not None, f"invalid {field}")
    _require(claimed == _digest({k: v for k, v in obj.items() if k != field}),
             f"{field} does not match canonical contents")


def _packets(manifest: Any) -> list[dict[str, Any]]:
    _require(type(manifest) is dict, "manifest must be an object")
    if manifest.get("schema") == SCHEMA:
        packets = [manifest]
    else:
        _require(manifest.get("schema") == BATCH_SCHEMA, "unknown manifest schema")
        _verify_digest(manifest, "batch_sha256")
        packets = manifest.get("packets")
        _require(type(packets) is list and 1 <= len(packets) <= 128, "invalid batch packets")
        _require(manifest.get("count") == len(packets), "incorrect batch count")
        targets = manifest.get("targets")
        _require(type(targets) is list and len(targets) == len(packets), "incorrect batch targets")
    previous = None
    for index, packet in enumerate(packets):
        _require(type(packet) is dict and packet.get("schema") == SCHEMA, "invalid packet schema")
        _verify_digest(packet, "packet_sha256")
        target = packet.get("target")
        roles = packet.get("roles")
        _require(type(target) is dict and type(roles) is list and len(roles) == len(ROLE_SPEC),
                 "invalid packet target or roles")
        key = _text(target.get("target_key"), "target_key", 256)
        _require(key == f"{target.get('repo', '').casefold()}#{target.get('issue_number')}",
                 "target_key disagrees with target")
        _require(previous is None or key > previous, "targets must be unique and ordered")
        previous = key
        operation = _text(packet.get("operation_id"), "operation_id", 128)
        for role, (name, _, _) in zip(roles, ROLE_SPEC):
            _require(type(role) is dict and role.get("role") == name and
                     role.get("lease_key") == f"{operation}:{name}" and
                     role.get("target_key") == key, "role identity mismatch")
        if manifest.get("schema") == BATCH_SCHEMA:
            _require(targets[index] == {"target_key": key, "operation_id": operation,
                                        "packet_sha256": packet["packet_sha256"]},
                     "batch target/packet mismatch")
    return packets


def _validated_receipt(value: Any, packets_by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    _require(type(value) is dict and value.get("schema") == RECEIPT_SCHEMA,
             "invalid role receipt schema")
    key = _text(value.get("target_key"), "receipt target_key", 256)
    _require(key in packets_by_key, "receipt targets an unknown bounty")
    packet = packets_by_key[key]
    role = _text(value.get("role"), "role", 16)
    names = [spec[0] for spec in ROLE_SPEC]
    _require(role in names, "unknown receipt role")
    role_number = names.index(role)
    role_packet = packet["roles"][role_number]
    _require(value.get("operation_id") == packet["operation_id"] and
             value.get("packet_sha256") == packet["packet_sha256"] and
             value.get("lease_key") == role_packet["lease_key"],
             "receipt operation, packet digest or lease mismatched")
    _require(value.get("owner") == role_packet["owner"] and
             role_packet["owner"] != "UNASSIGNED", "receipt owner not assigned")
    _require(value.get("receipt_type") == ROLE_SPEC[role_number][1] and
             value.get("status") == ROLE_SPEC[role_number][2],
             "receipt does not establish the required completion/acceptance")
    _text(value.get("receipt_id"), "receipt_id", 256)
    _require(SHA256.fullmatch(_text(value.get("evidence_sha256"), "evidence_sha256", 64)) is not None,
             "invalid evidence digest")
    if role in {"BUILD", "QA", "PUBLISH"}:
        _require(GIT_SHA.fullmatch(_text(value.get("head_sha"), "head_sha", 40)) is not None,
                 "missing exact source head")
    if role == "PUBLISH":
        # A literal PR link is evidence metadata, not proof of provider acceptance.
        pr_url = _text(value.get("publication_url"), "publication_url", 1024)
        _require(re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*", pr_url)
                 is not None and pr_url.lower().startswith(
                     "https://github.com/" + packet["target"]["repo"].lower() + "/pull/"),
                 "publication URL does not match the target repository")
    return value


def compile_mova_progress(manifest: Any, receipts: Any) -> dict[str, Any]:
    """Return one next actionable handoff per bounty, without any provider I/O."""
    packets = _packets(manifest)
    _require(type(receipts) is list and len(receipts) <= 640, "receipts must be a bounded list")
    by_key = {p["target"]["target_key"]: p for p in packets}
    completed: dict[tuple[str, str], dict[str, Any]] = {}
    receipt_ids: dict[str, tuple[str, str]] = {}
    for supplied in receipts:
        receipt = _validated_receipt(supplied, by_key)
        identity = (receipt["target_key"], receipt["role"])
        existing = completed.get(identity)
        _require(existing is None or existing == receipt, "conflicting role receipts")
        other = receipt_ids.get(receipt["receipt_id"])
        _require(other is None or other == identity, "receipt ID reused for another role")
        completed[identity] = receipt
        receipt_ids[receipt["receipt_id"]] = identity

    results = []
    for packet in packets:
        key = packet["target"]["target_key"]
        done = [completed.get((key, spec[0])) for spec in ROLE_SPEC]
        next_index = next((i for i, receipt in enumerate(done) if receipt is None), len(done))
        _require(not any(done[next_index:]), f"non-contiguous receipt sequence for {key}")
        if next_index >= 2:
            _require(done[2] is None or done[2]["head_sha"] == done[1]["head_sha"],
                     f"QA accepted a different head for {key}")
        if next_index >= 3:
            _require(done[3] is None or done[3]["head_sha"] == done[2]["head_sha"],
                     f"publication head differs from QA-accepted head for {key}")
        settlement_followup = None
        if next_index == len(ROLE_SPEC):
            # A COLLECT role receipt is only an operator assertion, never a
            # provider-verified award or payment. Assign independent verification
            # explicitly rather than silently treating the receivable as settled.
            status, next_role, role_packet = "SETTLEMENT_PROVIDER_RECHECK_REQUIRED", None, None
            collect_role = packet["roles"][-1]
            settlement_followup = {
                "recheck_status": "INDEPENDENT_PROVIDER_RECHECK_REQUIRED",
                "recheck_owner": "UNASSIGNED",
                "original_collect_owner": collect_role["owner"],
                "original_collect_account": collect_role["account"],
                "original_collect_lease_key": collect_role["lease_key"],
                "collect_receipt_id": done[-1]["receipt_id"],
                "collect_evidence_sha256": done[-1]["evidence_sha256"],
                "publication_url": done[3]["publication_url"],
                "publication_head": done[3]["head_sha"],
                "compensation_claim": packet["economics"]["compensation_claim"],
            }
        else:
            role_packet = packet["roles"][next_index]
            next_role = role_packet["role"]
            status = ("OWNER_REQUIRED" if role_packet["owner"] == "UNASSIGNED" else
                      "READY_FOR_INDEPENDENT_PROVIDER_RECHECK")
        results.append({
            "target_key": key,
            "operation_id": packet["operation_id"],
            "packet_sha256": packet["packet_sha256"],
            "completed_roles": [spec[0] for spec in ROLE_SPEC[:next_index]],
            "next_role": next_role,
            "status": status,
            "role_packet": role_packet,
            "source_head": done[1]["head_sha"] if next_index >= 2 else None,
            "award_state": "UNVERIFIED",
            "payment_state": "UNVERIFIED",
            "settlement_followup": settlement_followup,
        })
    core = {"schema": PROGRESS_SCHEMA, "count": len(results), "results": results,
            "authority": {"offline_only": True, "provider_reads": False,
                          "provider_writes": False, "receipts_provider_verified": False,
                          "provider_recheck_required_before_mutation": True}}
    return {**core, "progress_sha256": _digest(core)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advance a MOVA wave from bounded role receipts")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        receipts = json.loads(args.receipts.read_text(encoding="utf-8"))
        rendered = json.dumps(compile_mova_progress(manifest, receipts), indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    except (OSError, ValueError, UnicodeError):
        print("mova-progress: invalid manifest, receipt evidence, or output", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
