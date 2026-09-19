# SPDX-License-Identifier: MIT
"""Fail-closed compositor for GrantFox queue, source, dependency, and lifecycle receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .grantfox_application_continuity import verify_continuity_receipt
from .grantfox_dependency_fulfillment import verify_dependency_fulfillment_receipt
from .grantfox_dependency_readiness import verify_dependency_readiness_receipt
from .grantfox_queue_gate import verify_receipt as verify_queue_receipt
from .grantfox_source_readiness import verify_source_readiness_receipt

SCHEMA = "grantfox-activation-gate/v1"
AUTHORITY = {
    "advisory_only": True,
    "provider_application_authority": False,
    "implementation_write_authority": False,
    "submission_authority": False,
    "adjudication_authority": False,
    "payment_or_wallet_authority": False,
}
_RECEIPT_FIELDS = {
    "schema",
    "disposition",
    "advisory_next_action",
    "reason_codes",
    "identity",
    "anchors",
    "inputs",
    "evidence",
    "authority",
    "activation_receipt_sha256",
}
_EVIDENCE_FIELDS = {
    "queue_receipt",
    "source_receipt",
    "dependency_receipt",
    "fulfillment_receipt",
    "continuity_receipt",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class GrantFoxActivationInputError(ValueError):
    pass


def _obj(value: Any, name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise GrantFoxActivationInputError(f"{name} must be an object")
    return value


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise GrantFoxActivationInputError(f"{name} must be a non-empty trimmed string")
    return value


def _sha(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _identity(receipt: dict[str, Any], name: str, *, actor: bool) -> tuple[str, str, int, str | None]:
    ident = _obj(receipt.get("identity"), f"{name}.identity")
    owner = _text(ident.get("owner"), f"{name}.identity.owner").casefold()
    repo = _text(ident.get("repo"), f"{name}.identity.repo").casefold()
    number = ident.get("issue_number")
    if type(number) is not int or number < 1:
        raise GrantFoxActivationInputError(f"{name}.identity.issue_number must be a positive integer")
    login = None
    if actor:
        login = _text(ident.get("actor_login"), f"{name}.identity.actor_login").casefold()
    return owner, repo, number, login


def compile_activation(request: dict[str, Any]) -> dict[str, Any]:
    r = _obj(request, "request")
    if r.get("schema") != SCHEMA:
        raise GrantFoxActivationInputError(f"schema must equal {SCHEMA}")

    queue = _obj(r.get("queue_receipt"), "queue_receipt")
    source = _obj(r.get("source_receipt"), "source_receipt")
    dependency = _obj(r.get("dependency_receipt"), "dependency_receipt")
    fulfillment = _obj(r.get("fulfillment_receipt"), "fulfillment_receipt")
    continuity = _obj(r.get("continuity_receipt"), "continuity_receipt")

    if not verify_queue_receipt(queue, semantic=True):
        raise GrantFoxActivationInputError("queue_receipt does not verify semantically")
    if not verify_source_readiness_receipt(source):
        raise GrantFoxActivationInputError("source_receipt does not verify")
    if not verify_dependency_readiness_receipt(dependency):
        raise GrantFoxActivationInputError("dependency_receipt does not verify")
    if not verify_dependency_fulfillment_receipt(fulfillment):
        raise GrantFoxActivationInputError("fulfillment_receipt does not verify")
    if not verify_continuity_receipt(continuity, queue):
        raise GrantFoxActivationInputError(
            "continuity_receipt does not verify against queue semantics"
        )

    qid = _identity(queue, "queue_receipt", actor=False)
    sid = _identity(source, "source_receipt", actor=False)
    did = _identity(dependency, "dependency_receipt", actor=False)
    fid = _identity(fulfillment, "fulfillment_receipt", actor=False)
    cid = _identity(continuity, "continuity_receipt", actor=True)
    if (
        qid[:3] != sid[:3]
        or qid[:3] != did[:3]
        or qid[:3] != fid[:3]
        or qid[:3] != cid[:3]
    ):
        raise GrantFoxActivationInputError("receipts identify different issues")

    provider_snapshot = _obj(queue.get("provider_snapshot"), "queue_receipt.provider_snapshot")
    queue_actor = _text(
        provider_snapshot.get("actor_login"),
        "queue_receipt.provider_snapshot.actor_login",
    ).casefold()
    if queue_actor != cid[3]:
        raise GrantFoxActivationInputError("queue and continuity actors differ")

    queue_digest = _text(queue.get("receipt_sha256"), "queue_receipt.receipt_sha256")
    embedded = _obj(source.get("queue_receipt"), "source_receipt.queue_receipt")
    if embedded.get("receipt_sha256") != queue_digest or embedded != queue:
        raise GrantFoxActivationInputError("source receipt is anchored to a different queue receipt")
    dependency_source = _obj(
        dependency.get("source_receipt"), "dependency_receipt.source_receipt"
    )
    source_digest = _text(
        source.get("source_receipt_sha256"), "source_receipt.source_receipt_sha256"
    )
    if (
        dependency_source.get("source_receipt_sha256") != source_digest
        or dependency_source != source
    ):
        raise GrantFoxActivationInputError(
            "dependency receipt is anchored to a different source receipt"
        )

    fulfillment_dependency = _obj(
        fulfillment.get("dependency_receipt"),
        "fulfillment_receipt.dependency_receipt",
    )
    dependency_digest = _text(
        dependency.get("dependency_receipt_sha256"),
        "dependency_receipt.dependency_receipt_sha256",
    )
    if (
        fulfillment_dependency.get("dependency_receipt_sha256") != dependency_digest
        or fulfillment_dependency != dependency
    ):
        raise GrantFoxActivationInputError(
            "fulfillment receipt is anchored to a different dependency receipt"
        )

    anchor = _obj(continuity.get("queue_anchor"), "continuity_receipt.queue_anchor")
    if anchor.get("receipt_sha256") != queue_digest:
        raise GrantFoxActivationInputError("continuity receipt is anchored to a different queue receipt")

    qdisp = _text(queue.get("disposition"), "queue_receipt.disposition")
    sdisp = _text(source.get("source_disposition"), "source_receipt.source_disposition")
    ddisp = _text(
        dependency.get("dependency_disposition"),
        "dependency_receipt.dependency_disposition",
    )
    fdisp = _text(
        fulfillment.get("fulfillment_disposition"),
        "fulfillment_receipt.fulfillment_disposition",
    )
    cdisp = _text(continuity.get("disposition"), "continuity_receipt.disposition")
    state = _text(continuity.get("state"), "continuity_receipt.state")
    reasons: list[str] = []

    if cdisp == "HOLD_RECONCILE":
        disposition = "HOLD_RECONCILE"
        reasons.append("CONTINUITY_RECONCILE_REQUIRED")
    elif sdisp == "HOLD":
        disposition = "HOLD_SOURCE"
        reasons.append("SOURCE_HOLD")
    elif state in {"SUBMITTED", "APPROVED", "PAYMENT_SENT", "PAID", "REJECTED"}:
        disposition = "HOLD_RECONCILE"
        reasons.append("LIFECYCLE_ALREADY_PAST_IMPLEMENTATION")
    elif ddisp == "HOLD":
        disposition = "HOLD_DEPENDENCIES"
        reasons.append("DEPENDENCY_READINESS_HOLD")
    elif state == "ASSIGNED":
        if qdisp != "IMPLEMENTATION_ELIGIBLE":
            disposition = "HOLD_RECONCILE"
            reasons.append("ASSIGNMENT_QUEUE_CONTRADICTION")
        elif sdisp != "SOURCE_ALIGNED":
            disposition = "HOLD_SOURCE"
            reasons.append("ASSIGNED_SCOPE_SOURCE_NOT_ALIGNED")
        elif ddisp == "DEPENDENCY_WAIT":
            disposition = "WAIT_DEPENDENCIES"
            reasons.append("PREREQUISITE_ISSUES_OPEN")
        elif ddisp != "DEPENDENCIES_CLEAR":
            disposition = "HOLD_RECONCILE"
            reasons.append("DEPENDENCY_RECEIPT_CONTRADICTION")
        elif fdisp == "FULFILLMENT_WAIT":
            disposition = "WAIT_DEPENDENCIES"
            reasons.append("PREREQUISITE_LANDING_EVIDENCE_MISSING")
        elif fdisp == "HOLD":
            disposition = "HOLD_DEPENDENCIES"
            reasons.append("DEPENDENCY_FULFILLMENT_HOLD")
        elif fdisp != "DEPENDENCIES_FULFILLED":
            disposition = "HOLD_RECONCILE"
            reasons.append("FULFILLMENT_RECEIPT_CONTRADICTION")
        else:
            disposition = "IMPLEMENT_ASSIGNED_SCOPE"
    elif state == "APPLIED":
        if qdisp not in {"WAIT_ASSIGNMENT", "APPLY_ELIGIBLE"}:
            disposition = "HOLD_RECONCILE"
            reasons.append("APPLIED_QUEUE_CONTRADICTION")
        else:
            disposition = "WAIT_ASSIGNMENT"
    elif state == "DISCOVERED":
        if qdisp == "APPLY_ELIGIBLE" and sdisp == "SOURCE_ALIGNED":
            disposition = "APPLY_ELIGIBLE"
        elif qdisp == "APPLY_ELIGIBLE" and sdisp == "SOURCE_DRIFT_REPLAN":
            disposition = "REPLAN_BEFORE_APPLY"
        else:
            disposition = "HOLD_RECONCILE"
            reasons.append("DISCOVERED_RECEIPT_CONTRADICTION")
    else:
        disposition = "HOLD_RECONCILE"
        reasons.append("UNSUPPORTED_LIFECYCLE_STATE")

    actions = {
        "APPLY_ELIGIBLE": "APPLY_THROUGH_VERIFIED_PROVIDER_ROUTE",
        "REPLAN_BEFORE_APPLY": "REPLAN_FROM_PINNED_SOURCE_THEN_REFRESH_GATE",
        "WAIT_ASSIGNMENT": "WAIT_FOR_DURABLE_PROVIDER_ASSIGNMENT",
        "WAIT_DEPENDENCIES": "WAIT_FOR_PREREQUISITES_BEFORE_IMPLEMENTATION",
        "IMPLEMENT_ASSIGNED_SCOPE": "IMPLEMENT_ONLY_THE_ASSIGNED_PINNED_SCOPE",
        "HOLD_SOURCE": "REFRESH_OR_RECONCILE_PINNED_SOURCE",
        "HOLD_DEPENDENCIES": "REFRESH_OR_RECONCILE_DEPENDENCY_EVIDENCE",
        "HOLD_RECONCILE": "STOP_AND_RECONCILE_RECEIPTS",
    }

    body = {
        "schema": SCHEMA,
        "disposition": disposition,
        "advisory_next_action": actions[disposition],
        "reason_codes": reasons,
        "identity": {
            "owner": qid[0],
            "repo": qid[1],
            "issue_number": qid[2],
            "actor_login": queue_actor,
        },
        "anchors": {
            "queue_receipt_sha256": queue_digest,
            "source_receipt_sha256": source_digest,
            "dependency_receipt_sha256": dependency_digest,
            "fulfillment_receipt_sha256": _text(
                fulfillment.get("fulfillment_receipt_sha256"),
                "fulfillment_receipt.fulfillment_receipt_sha256",
            ),
            "continuity_receipt_sha256": _text(continuity.get("receipt_sha256"), "continuity_receipt.receipt_sha256"),
        },
        "inputs": {
            "queue_disposition": qdisp,
            "source_disposition": sdisp,
            "dependency_disposition": ddisp,
            "fulfillment_disposition": fdisp,
            "continuity_disposition": cdisp,
            "continuity_state": state,
        },
        "evidence": {
            "queue_receipt": queue,
            "source_receipt": source,
            "dependency_receipt": dependency,
            "fulfillment_receipt": fulfillment,
            "continuity_receipt": continuity,
        },
        "authority": AUTHORITY,
    }
    return {**body, "activation_receipt_sha256": _sha(body)}


def verify_activation_receipt(receipt: dict[str, Any]) -> bool:
    """Verify native evidence and recompile the complete activation semantics."""
    if type(receipt) is not dict or set(receipt) != _RECEIPT_FIELDS:
        return False
    if receipt.get("schema") != SCHEMA or receipt.get("authority") != AUTHORITY:
        return False

    digest = receipt.get("activation_receipt_sha256")
    if type(digest) is not str or _SHA256.fullmatch(digest) is None:
        return False

    evidence = receipt.get("evidence")
    if type(evidence) is not dict or set(evidence) != _EVIDENCE_FIELDS:
        return False
    queue = evidence.get("queue_receipt")
    source = evidence.get("source_receipt")
    dependency = evidence.get("dependency_receipt")
    fulfillment = evidence.get("fulfillment_receipt")
    continuity = evidence.get("continuity_receipt")
    if (
        type(queue) is not dict
        or type(source) is not dict
        or type(dependency) is not dict
        or type(fulfillment) is not dict
        or type(continuity) is not dict
    ):
        return False

    try:
        expected = compile_activation(
            {
                "schema": SCHEMA,
                "queue_receipt": queue,
                "source_receipt": source,
                "dependency_receipt": dependency,
                "fulfillment_receipt": fulfillment,
                "continuity_receipt": continuity,
            }
        )
    except (GrantFoxActivationInputError, KeyError, TypeError, ValueError):
        return False
    return expected == receipt


def format_summary(receipt: dict[str, Any]) -> str:
    i = receipt["identity"]
    reasons = ",".join(receipt["reason_codes"]) or "none"
    return (
        f"activation={receipt['disposition']} "
        f"issue={i['owner']}/{i['repo']}#{i['issue_number']} actor={i['actor_login']} "
        f"next={receipt['advisory_next_action']} reasons={reasons} "
        f"activation_receipt_sha256={receipt['activation_receipt_sha256']}"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m concierge.grantfox_activation_gate")
    p.add_argument("request", help="activation request JSON path, or - for stdin")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        if a.request == "-":
            import sys
            payload = json.load(sys.stdin)
        else:
            with Path(a.request).open(encoding="utf-8") as f:
                payload = json.load(f)
        receipt = compile_activation(payload)
    except (OSError, json.JSONDecodeError, GrantFoxActivationInputError) as exc:
        p.error(str(exc))
    print(json.dumps(receipt, indent=2, sort_keys=True) if a.json else format_summary(receipt))
    return 2 if receipt["disposition"].startswith("HOLD_") else 0


if __name__ == "__main__":
    raise SystemExit(main())
