# SPDX-License-Identifier: MIT
"""Deterministic, evidence-only procurement/RFP qualification gate.

BidGate deliberately does not contact buyers, submit proposals, sign contracts,
spend money, or recognize revenue. It turns explicit opportunity requirements
and explicit participant evidence into a content-addressed human decision aid.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "bidgate/v1"
DISPOSITIONS = ("PRIME_READY", "TEAM_REQUIRED", "NO_GO", "HOLD")
_LEVELS = {"mandatory", "scored"}
_AUTHORITIES = {"prime_only", "partner_allowed"}
_ROLES = {"prime", "partner"}
_OPS = {"present", "equals", "gte"}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class BidGateInputError(ValueError):
    """Raised when a qualification manifest is structurally unreliable."""


def _obj(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BidGateInputError(f"{name} must be an object")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise BidGateInputError(f"{name} must be a list")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BidGateInputError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _text(value, name)


def _integer(value: Any, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BidGateInputError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise BidGateInputError(f"{name} must be >= {minimum}")
    return value


def _timestamp(value: Any, name: str) -> datetime:
    text = _text(value, name)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise BidGateInputError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BidGateInputError(f"{name} must include a timezone")
    return parsed


def _scalar(value: Any, name: str) -> str | int | bool:
    # Floats are intentionally excluded: no NaN/infinity/binary-FP authority.
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value:
        return value
    raise BidGateInputError(
        f"{name} must be a non-empty string, integer, or boolean"
    )


def _canonical_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise BidGateInputError("manifest contains non-canonical JSON data") from exc
    return text.encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _normalized_rule(raw: Any, name: str) -> dict[str, Any]:
    rule = _obj(raw, name)
    op = _text(rule.get("op"), f"{name}.op")
    if op not in _OPS:
        raise BidGateInputError(f"{name}.op must be one of {sorted(_OPS)}")
    normalized: dict[str, Any] = {"op": op}
    if op == "present":
        if "value" in rule:
            raise BidGateInputError(f"{name}.value is invalid for present")
    elif op == "equals":
        normalized["value"] = _scalar(rule.get("value"), f"{name}.value")
    else:
        normalized["value"] = _integer(rule.get("value"), f"{name}.value")
    extra = set(rule) - {"op", "value"}
    if extra:
        raise BidGateInputError(f"{name} has unsupported keys: {sorted(extra)}")
    return normalized


def _normalized_requirement(raw: Any, index: int) -> dict[str, Any]:
    req = _obj(raw, f"opportunity.requirements[{index}]")
    prefix = f"opportunity.requirements[{index}]"
    req_id = _text(req.get("id"), f"{prefix}.id")
    level = _text(req.get("level"), f"{prefix}.level")
    if level not in _LEVELS:
        raise BidGateInputError(f"{prefix}.level must be one of {sorted(_LEVELS)}")
    authority = _text(req.get("authority"), f"{prefix}.authority")
    if authority not in _AUTHORITIES:
        raise BidGateInputError(
            f"{prefix}.authority must be one of {sorted(_AUTHORITIES)}"
        )
    evidence_type = _text(req.get("evidence_type"), f"{prefix}.evidence_type")
    rule = _normalized_rule(req.get("rule"), f"{prefix}.rule")
    question = _optional_text(req.get("question"), f"{prefix}.question")

    normalized: dict[str, Any] = {
        "id": req_id,
        "level": level,
        "authority": authority,
        "evidence_type": evidence_type,
        "rule": rule,
    }
    if question is not None:
        normalized["question"] = question

    if level == "scored":
        normalized["weight"] = _integer(
            req.get("weight"), f"{prefix}.weight", minimum=1
        )
    elif "weight" in req:
        raise BidGateInputError(
            f"{prefix}.weight is valid only for scored requirements"
        )

    allowed = {
        "id", "level", "authority", "evidence_type", "rule", "question", "weight"
    }
    extra = set(req) - allowed
    if extra:
        raise BidGateInputError(f"{prefix} has unsupported keys: {sorted(extra)}")
    return normalized


def _normalized_opportunity(raw: Any) -> tuple[dict[str, Any], datetime]:
    opp = _obj(raw, "opportunity")
    decision_time = _timestamp(opp.get("decision_time"), "opportunity.decision_time")
    proposal_deadline = _timestamp(
        opp.get("proposal_deadline"), "opportunity.proposal_deadline"
    )
    question_deadline_text = opp.get("question_deadline")
    question_deadline = (
        _timestamp(question_deadline_text, "opportunity.question_deadline")
        if question_deadline_text is not None
        else None
    )
    if question_deadline is not None and question_deadline > proposal_deadline:
        raise BidGateInputError("question_deadline cannot be after proposal_deadline")

    requirements = [
        _normalized_requirement(item, index)
        for index, item in enumerate(
            _list(opp.get("requirements"), "opportunity.requirements")
        )
    ]
    if not requirements:
        raise BidGateInputError("opportunity.requirements must not be empty")
    ids = [item["id"] for item in requirements]
    if len(ids) != len(set(ids)):
        raise BidGateInputError("opportunity requirement ids must be unique")

    max_score = sum(item.get("weight", 0) for item in requirements)
    minimum_score = _integer(
        opp.get("minimum_score", 0), "opportunity.minimum_score", minimum=0
    )
    if minimum_score > max_score:
        raise BidGateInputError(
            "opportunity.minimum_score exceeds total scored weight"
        )

    normalized: dict[str, Any] = {
        "id": _text(opp.get("id"), "opportunity.id"),
        "title": _text(opp.get("title"), "opportunity.title"),
        "source": _text(opp.get("source"), "opportunity.source"),
        "decision_time": decision_time.isoformat(),
        "proposal_deadline": proposal_deadline.isoformat(),
        "contact_policy": _text(
            opp.get("contact_policy"), "opportunity.contact_policy"
        ),
        "minimum_score": minimum_score,
        "requirements": requirements,
    }
    if question_deadline is not None:
        normalized["question_deadline"] = question_deadline.isoformat()

    allowed = {
        "id", "title", "source", "decision_time", "question_deadline",
        "proposal_deadline", "contact_policy", "minimum_score", "requirements",
    }
    extra = set(opp) - allowed
    if extra:
        raise BidGateInputError(f"opportunity has unsupported keys: {sorted(extra)}")
    return normalized, decision_time


def _normalized_evidence(
    raw: Any, *, participant_id: str, index: int, decision_time: datetime
) -> dict[str, Any]:
    ev = _obj(raw, f"participant {participant_id} evidence[{index}]")
    prefix = f"participant {participant_id} evidence[{index}]"
    observed = _timestamp(ev.get("observed_at"), f"{prefix}.observed_at")
    if observed > decision_time:
        raise BidGateInputError(
            f"{prefix}.observed_at cannot be after decision_time"
        )
    expires_text = ev.get("expires_at")
    expires = (
        _timestamp(expires_text, f"{prefix}.expires_at")
        if expires_text is not None
        else None
    )
    if expires is not None and expires <= observed:
        raise BidGateInputError(f"{prefix}.expires_at must be after observed_at")
    provenance = _text(
        ev.get("provenance_sha256"), f"{prefix}.provenance_sha256"
    )
    if not _HEX64.fullmatch(provenance):
        raise BidGateInputError(
            f"{prefix}.provenance_sha256 must be lowercase sha256 hex"
        )

    normalized: dict[str, Any] = {
        "id": _text(ev.get("id"), f"{prefix}.id"),
        "type": _text(ev.get("type"), f"{prefix}.type"),
        "value": _scalar(ev.get("value"), f"{prefix}.value"),
        "source": _text(ev.get("source"), f"{prefix}.source"),
        "observed_at": observed.isoformat(),
        "provenance_sha256": provenance,
    }
    if expires is not None:
        normalized["expires_at"] = expires.isoformat()

    allowed = {
        "id", "type", "value", "source", "observed_at", "expires_at",
        "provenance_sha256",
    }
    extra = set(ev) - allowed
    if extra:
        raise BidGateInputError(f"{prefix} has unsupported keys: {sorted(extra)}")
    return normalized


def _normalized_participants(raw: Any, decision_time: datetime) -> list[dict[str, Any]]:
    participants: list[dict[str, Any]] = []
    participant_ids: set[str] = set()
    evidence_ids: set[str] = set()
    prime_count = 0

    for index, item in enumerate(_list(raw, "participants")):
        part = _obj(item, f"participants[{index}]")
        prefix = f"participants[{index}]"
        participant_id = _text(part.get("id"), f"{prefix}.id")
        if participant_id in participant_ids:
            raise BidGateInputError("participant ids must be unique")
        participant_ids.add(participant_id)
        role = _text(part.get("role"), f"{prefix}.role")
        if role not in _ROLES:
            raise BidGateInputError(f"{prefix}.role must be one of {sorted(_ROLES)}")
        if role == "prime":
            prime_count += 1
        evidence = [
            _normalized_evidence(
                ev,
                participant_id=participant_id,
                index=eidx,
                decision_time=decision_time,
            )
            for eidx, ev in enumerate(
                _list(part.get("evidence"), f"{prefix}.evidence")
            )
        ]
        for ev in evidence:
            if ev["id"] in evidence_ids:
                raise BidGateInputError(
                    "evidence ids must be unique across participants"
                )
            evidence_ids.add(ev["id"])
        extra = set(part) - {"id", "role", "evidence"}
        if extra:
            raise BidGateInputError(f"{prefix} has unsupported keys: {sorted(extra)}")
        participants.append({"id": participant_id, "role": role, "evidence": evidence})

    if prime_count != 1:
        raise BidGateInputError("participants must contain exactly one prime")
    participants.sort(key=lambda item: (item["role"] != "prime", item["id"]))
    return participants


def _satisfies(value: str | int | bool, rule: dict[str, Any]) -> bool:
    op = rule["op"]
    if op == "present":
        return True
    if op == "equals":
        return type(value) is type(rule["value"]) and value == rule["value"]
    if op == "gte":
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value >= rule["value"]
        )
    raise AssertionError(op)


def _evidence_state(
    participant: dict[str, Any],
    evidence_type: str,
    decision_time: datetime,
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Return usable atoms plus (expired_present, ambiguous_usable)."""
    matching = [
        item for item in participant["evidence"] if item["type"] == evidence_type
    ]
    usable: list[dict[str, Any]] = []
    expired = False
    for item in matching:
        expires_text = item.get("expires_at")
        if expires_text is not None and decision_time >= _timestamp(
            expires_text, "normalized expires_at"
        ):
            expired = True
            continue
        usable.append(item)
    values = {
        (type(item["value"]).__name__, json.dumps(item["value"], sort_keys=True))
        for item in usable
    }
    ambiguous = len(values) > 1
    return usable, expired, ambiguous


def _candidate_summary(
    requirement: dict[str, Any],
    participants: list[dict[str, Any]],
    decision_time: datetime,
) -> dict[str, Any]:
    prime = next(item for item in participants if item["role"] == "prime")
    considered: list[dict[str, Any]] = [prime]
    if requirement["authority"] == "partner_allowed":
        considered.extend(item for item in participants if item["role"] == "partner")

    uncertainty_codes: set[str] = set()
    satisfiers: list[tuple[str, str]] = []
    observed_any = False
    for participant in considered:
        usable, expired, ambiguous = _evidence_state(
            participant, requirement["evidence_type"], decision_time
        )
        observed_any = observed_any or bool(usable) or expired
        if expired:
            uncertainty_codes.add("EXPIRED_EVIDENCE")
        if ambiguous:
            uncertainty_codes.add("AMBIGUOUS_EVIDENCE")
            continue
        if any(
            _satisfies(item["value"], requirement["rule"])
            for item in usable
        ):
            satisfiers.append((participant["id"], participant["role"]))

    # A current unambiguous satisfier is authoritative even if an older atom expired.
    if satisfiers:
        prime_satisfier = next(
            (item for item in satisfiers if item[1] == "prime"), None
        )
        chosen = prime_satisfier or sorted(satisfiers)[0]
        return {
            "satisfied": True,
            "participant_id": chosen[0],
            "participant_role": chosen[1],
            "uncertainty_codes": [],
            "observed_any": observed_any,
        }

    return {
        "satisfied": False,
        "participant_id": None,
        "participant_role": None,
        "uncertainty_codes": sorted(uncertainty_codes),
        "observed_any": observed_any,
    }


def qualify_bid(manifest: dict[str, Any]) -> dict[str, Any]:
    """Qualify one explicit procurement opportunity with no inferred evidence."""
    root = _obj(manifest, "manifest")
    extra = set(root) - {"opportunity", "participants"}
    if extra:
        raise BidGateInputError(f"manifest has unsupported keys: {sorted(extra)}")

    opportunity, decision_time = _normalized_opportunity(root.get("opportunity"))
    participants = _normalized_participants(root.get("participants"), decision_time)
    opportunity_digest = _digest(opportunity)
    evidence_digest = _digest(participants)

    requirement_results: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    hold_reasons: list[dict[str, str]] = []
    partner_gaps: list[str] = []
    questions: list[dict[str, str]] = []
    score = 0
    max_score = 0
    team_required = False

    proposal_deadline = _timestamp(
        opportunity["proposal_deadline"], "normalized proposal_deadline"
    )
    if decision_time >= proposal_deadline:
        blockers.append(
            {
                "code": "PROPOSAL_DEADLINE_PASSED",
                "requirement_id": "__deadline__",
                "message": "Decision time is at or after the proposal deadline.",
            }
        )

    for requirement in opportunity["requirements"]:
        summary = _candidate_summary(requirement, participants, decision_time)
        result: dict[str, Any] = {
            "id": requirement["id"],
            "level": requirement["level"],
            "authority": requirement["authority"],
            "satisfied": summary["satisfied"],
            "satisfied_by": summary["participant_id"],
            "satisfied_role": summary["participant_role"],
            "uncertainty_codes": summary["uncertainty_codes"],
        }

        if requirement["level"] == "scored":
            weight = requirement["weight"]
            max_score += weight
            awarded = weight if summary["satisfied"] else 0
            score += awarded
            result["weight"] = weight
            result["awarded"] = awarded

        if summary["satisfied"] and summary["participant_role"] == "partner":
            team_required = True
            partner_gaps.append(requirement["id"])

        if not summary["satisfied"]:
            if summary["uncertainty_codes"]:
                for code in summary["uncertainty_codes"]:
                    hold_reasons.append(
                        {
                            "code": code,
                            "requirement_id": requirement["id"],
                            "message": (
                                "Referenced evidence is not currently authoritative."
                            ),
                        }
                    )
            elif requirement["level"] == "mandatory":
                blockers.append(
                    {
                        "code": "MANDATORY_GATE_UNSATISFIED",
                        "requirement_id": requirement["id"],
                        "message": (
                            "No authoritative evidence satisfies this mandatory gate."
                        ),
                    }
                )

            question = requirement.get("question")
            q_deadline_text = opportunity.get("question_deadline")
            if question and q_deadline_text is not None:
                q_deadline = _timestamp(
                    q_deadline_text, "normalized question_deadline"
                )
                if decision_time < q_deadline:
                    questions.append(
                        {
                            "requirement_id": requirement["id"],
                            "question": question,
                            "deadline": q_deadline.isoformat(),
                        }
                    )

        requirement_results.append(result)

    minimum_score = opportunity["minimum_score"]
    if score < minimum_score:
        blockers.append(
            {
                "code": "MINIMUM_SCORE_UNMET",
                "requirement_id": "__score__",
                "message": (
                    f"Scored evidence awards {score}/{max_score}; "
                    f"minimum is {minimum_score}."
                ),
            }
        )

    mandatory_blockers = [
        item
        for item in blockers
        if item["code"]
        in {"MANDATORY_GATE_UNSATISFIED", "PROPOSAL_DEADLINE_PASSED"}
    ]
    if mandatory_blockers:
        disposition = "NO_GO"
    elif hold_reasons:
        disposition = "HOLD"
    elif blockers:
        disposition = "NO_GO"
    elif team_required:
        disposition = "TEAM_REQUIRED"
    else:
        disposition = "PRIME_READY"

    safe_action = {
        "PRIME_READY": "PREPARE_PROPOSAL_FOR_HUMAN_APPROVAL",
        "TEAM_REQUIRED": "PREPARE_TEAMING_PACKAGE_FOR_HUMAN_APPROVAL",
        "NO_GO": "STOP_NO_GO",
        "HOLD": "HOLD_FOR_EVIDENCE_OR_CLARIFICATION",
    }[disposition]

    decision_core: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "opportunity_id": opportunity["id"],
        "disposition": disposition,
        "score": score,
        "max_score": max_score,
        "minimum_score": minimum_score,
        "requirements": requirement_results,
        "blockers": sorted(
            blockers, key=lambda item: (item["requirement_id"], item["code"])
        ),
        "hold_reasons": sorted(
            hold_reasons, key=lambda item: (item["requirement_id"], item["code"])
        ),
        "partner_satisfied_requirements": sorted(set(partner_gaps)),
        "clarification_questions": sorted(
            questions, key=lambda item: item["requirement_id"]
        ),
        "contact_policy": opportunity["contact_policy"],
        "safe_next_action": safe_action,
        "authority": {
            "buyer_contact_authorized": False,
            "submission_authorized": False,
            "signature_authorized": False,
            "contract_authorized": False,
            "spend_authorized": False,
            "revenue_recognition_authorized": False,
        },
        "opportunity_digest": opportunity_digest,
        "evidence_digest": evidence_digest,
    }
    decision_core["decision_digest"] = _digest(decision_core)
    return decision_core


def qualify_file(path: str | Path) -> dict[str, Any]:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise BidGateInputError(f"cannot load manifest: {exc}") from exc
    return qualify_bid(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bidgate",
        description="Deterministic evidence-only RFP qualification gate.",
    )
    parser.add_argument("manifest", help="Path to a BidGate JSON manifest")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = qualify_file(args.manifest)
    except BidGateInputError as exc:
        print(f"bidgate input error: {exc}", file=sys.stderr)
        return 64
    print(
        json.dumps(
            result,
            sort_keys=True,
            indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
        )
    )
    if result["disposition"] == "HOLD":
        return 2
    if result["disposition"] == "NO_GO":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
