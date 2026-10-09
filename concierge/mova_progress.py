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


def _role_specs_for_packet(packet: dict[str, Any]) -> tuple[tuple[str, str, str], ...]:
    """Honor only the two factory-emitted role shapes, never arbitrary omissions."""
    roles = packet.get("roles")
    _require(type(roles) is list, "missing packet roles")
    names = [role.get("role") if type(role) is dict else None for role in roles]
    default = [spec[0] for spec in ROLE_SPEC]
    if names == default:
        return ROLE_SPEC

    _require(names == ["SCOUT", "BUILD", "PUBLISH", "COLLECT"],
             "invalid MOVA packet role sequence")
    evidence = packet.get("evidence")
    target = packet.get("target")
    coordination = packet.get("coordination")
    _require(type(evidence) is dict and type(target) is dict and
             type(coordination) is dict, "missing optional-QA provenance")
    waiver = evidence.get("qa_handoff")
    _require(type(waiver) is dict and set(waiver) == {
        "schema", "separate_qa_required", "issue_url", "capture_sha256",
        "checked_at", "builder_focused_check",
    }, "invalid optional-QA source evidence")
    _require(waiver.get("schema") == "mova-independent-qa/v1" and
             waiver.get("separate_qa_required") is False and
             waiver.get("builder_focused_check") is True and
             waiver.get("issue_url") == target.get("canonical_issue_url") and
             waiver.get("capture_sha256") == evidence.get("canonical_capture_sha256") and
             SHA256.fullmatch(str(waiver.get("capture_sha256"))) is not None and
             coordination.get("independent_qa_handoff") ==
             "NOT_REQUIRED_WITH_SOURCE_EVIDENCE" and
             coordination.get("builder_focused_check_still_required") is True and
             roles[2].get("depends_on") == ["BUILD_RECEIPT"],
             "optional-QA source receipt or publish dependency is invalid")
    _text(waiver.get("checked_at"), "optional-QA checked_at", 64)
    return tuple(spec for spec in ROLE_SPEC if spec[0] != "QA")


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
        _require(type(target) is dict and type(roles) is list,
                 "invalid packet target or roles")
        stage_specs = _role_specs_for_packet(packet)
        key = _text(target.get("target_key"), "target_key", 256)
        _require(key == f"{target.get('repo', '').casefold()}#{target.get('issue_number')}",
                 "target_key disagrees with target")
        _require(previous is None or key > previous, "targets must be unique and ordered")
        previous = key
        operation = _text(packet.get("operation_id"), "operation_id", 128)
        for role, (name, _, _) in zip(roles, stage_specs):
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
    stage_specs = _role_specs_for_packet(packet)
    role_index = next((i for i, spec in enumerate(stage_specs) if spec[0] == role), None)
    _require(role_index is not None, "receipt role is not present in source packet")
    role_packet = packet["roles"][role_index]
    role_spec = stage_specs[role_index]
    _require(value.get("operation_id") == packet["operation_id"] and
             value.get("packet_sha256") == packet["packet_sha256"] and
             value.get("lease_key") == role_packet["lease_key"],
             "receipt operation, packet digest or lease mismatched")
    _require(value.get("owner") == role_packet["owner"] and
             role_packet["owner"] != "UNASSIGNED", "receipt owner not assigned")
    _require(value.get("receipt_type") == role_spec[1] and
             value.get("status") == role_spec[2],
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
        stage_specs = _role_specs_for_packet(packet)
        done = [completed.get((key, spec[0])) for spec in stage_specs]
        by_role = {spec[0]: receipt for spec, receipt in zip(stage_specs, done)}
        next_index = next((i for i, receipt in enumerate(done) if receipt is None), len(done))
        _require(not any(done[next_index:]), f"non-contiguous receipt sequence for {key}")
        built = by_role["BUILD"]
        qa = by_role.get("QA")
        published = by_role["PUBLISH"]
        if qa is not None:
            _require(built is not None and qa["head_sha"] == built["head_sha"],
                     f"QA accepted a different head for {key}")
        if published is not None:
            accepted = qa if "QA" in by_role else built
            _require(accepted is not None and published["head_sha"] == accepted["head_sha"],
                     f"publication head differs from accepted source head for {key}")
        settlement_followup = None
        if next_index == len(stage_specs):
            # A COLLECT role receipt is only an operator assertion, never a
            # provider-verified award or payment. Assign independent verification
            # explicitly rather than silently treating the receivable as settled.
            status, next_role, role_packet = "SETTLEMENT_PROVIDER_RECHECK_REQUIRED", None, None
            economics = packet.get("economics")
            _require(type(economics) is dict, "missing original bounty economics")
            original_claim = economics.get("compensation_claim")
            _require(type(original_claim) is dict and original_claim.get("required") is True
                     and type(original_claim.get("text")) is str
                     and bool(original_claim["text"].strip()),
                     "missing active original compensation claim")
            collect_role = packet["roles"][-1]
            settlement_followup = {
                "recheck_status": "INDEPENDENT_PROVIDER_RECHECK_REQUIRED",
                "recheck_owner": "UNASSIGNED",
                "original_collect_owner": collect_role["owner"],
                "original_collect_account": collect_role["account"],
                "original_collect_lease_key": collect_role["lease_key"],
                "collect_receipt_id": by_role["COLLECT"]["receipt_id"],
                "collect_evidence_sha256": by_role["COLLECT"]["evidence_sha256"],
                "publication_url": published["publication_url"],
                "publication_head": published["head_sha"],
                "compensation_claim": original_claim,
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
            "completed_roles": [spec[0] for spec in stage_specs[:next_index]],
            "next_role": next_role,
            "status": status,
            "role_packet": role_packet,
            "source_head": built["head_sha"] if built is not None else None,
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
