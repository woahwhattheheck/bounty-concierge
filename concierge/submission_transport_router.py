# SPDX-License-Identifier: MIT
"""Evidence-bound, send-free routing for bounty submission transport failover.

This module lives between ``submission_packet`` readiness and an external
provider mutation.  It never sends, never infers sponsor acceptance, and never
turns a timeout/unknown result into permission to try a second route.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple


class SubmissionTransportInputError(ValueError):
    """Raised when transport evidence is structurally unsafe or inconsistent."""


_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_FAILURE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,127}$")
_MAX_ROUTES = 16
_MAX_ATTEMPTS = 16
_MAX_TEXT = 1024

_PACKET_KEYS = {
    "canonical_source_url",
    "advertised_reward_usd",
    "disposition",
    "reason_codes",
    "evidence",
    "authority",
    "packet_sha256",
}
_POLICY_KEYS = {
    "schema",
    "policy_id",
    "canonical_source_url",
    "packet_sha256",
    "policy_evidence_sha256",
    "routes",
    "policy_sha256",
}
_ROUTE_KEYS = {"route_id", "route_class", "route_evidence_sha256", "failover_on"}
_ATTEMPT_KEYS = {
    "attempt_id",
    "route_id",
    "packet_sha256",
    "head_sha",
    "artifact_evidence_sha256",
    "outcome",
    "failure_class",
    "provider_evidence_sha256",
}
_ALLOWED_OUTCOMES = {"FAILED_CONFIRMED", "SUCCESS_CONFIRMED", "AMBIGUOUS"}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _bounded_text(value: Any, name: str) -> str:
    if type(value) is not str or not value or len(value) > _MAX_TEXT:
        raise SubmissionTransportInputError(f"{name} must be a non-empty bounded string")
    if "\x00" in value or any(ord(ch) < 32 for ch in value):
        raise SubmissionTransportInputError(f"{name} contains control characters")
    return value


def _sha40(value: Any, name: str) -> str:
    if type(value) is not str or not _SHA40_RE.fullmatch(value):
        raise SubmissionTransportInputError(f"{name} must be exactly 40 lowercase hex characters")
    return value


def _sha256_text(value: Any, name: str) -> str:
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        raise SubmissionTransportInputError(f"{name} must be exactly 64 lowercase hex characters")
    return value


def _token(value: Any, name: str) -> str:
    if type(value) is not str or not _TOKEN_RE.fullmatch(value):
        raise SubmissionTransportInputError(f"{name} must be a canonical lowercase token")
    return value


def _failure(value: Any, name: str) -> str:
    if type(value) is not str or not _FAILURE_RE.fullmatch(value):
        raise SubmissionTransportInputError(f"{name} must be a canonical uppercase failure token")
    return value


def _check_packet(packet: Any) -> dict[str, Any]:
    if type(packet) is not dict or set(packet) != _PACKET_KEYS:
        raise SubmissionTransportInputError("packet must be one exact bounty-submission-packet/v1 inner packet")
    if packet["disposition"] != "READY_FOR_HUMAN_SUBMISSION":
        raise SubmissionTransportInputError("packet is not READY_FOR_HUMAN_SUBMISSION")
    if packet["reason_codes"] != []:
        raise SubmissionTransportInputError("ready packet must have no reason codes")
    source = _bounded_text(packet["canonical_source_url"], "canonical_source_url")
    if not source.startswith("https://github.com/") or "/issues/" not in source:
        raise SubmissionTransportInputError("canonical_source_url must be a canonical GitHub issue URL")

    authority = packet["authority"]
    if type(authority) is not dict:
        raise SubmissionTransportInputError("packet authority is missing")
    if (
        authority.get("submission") != "human_only"
        or authority.get("acceptance") != "not_inferred"
        or authority.get("payout") != "not_inferred"
        or authority.get("cash_claim") is not False
    ):
        raise SubmissionTransportInputError("packet authority exceeds human-only submission scope")

    evidence = packet["evidence"]
    if type(evidence) is not dict:
        raise SubmissionTransportInputError("ready packet evidence is missing")
    head_sha = _sha40(evidence.get("head_sha"), "packet evidence head_sha")
    artifact_evidence_sha = _sha256_text(
        evidence.get("evidence_sha256"), "packet evidence evidence_sha256"
    )

    supplied = _sha256_text(packet["packet_sha256"], "packet_sha256")
    core = dict(packet)
    del core["packet_sha256"]
    if _sha256(core) != supplied:
        raise SubmissionTransportInputError("packet_sha256 does not match canonical packet bytes")

    return {
        "canonical_source_url": source,
        "packet_sha256": supplied,
        "head_sha": head_sha,
        "artifact_evidence_sha256": artifact_evidence_sha,
    }


def _check_policy(policy: Any, packet: dict[str, Any]) -> dict[str, Any]:
    if type(policy) is not dict or set(policy) != _POLICY_KEYS:
        raise SubmissionTransportInputError("policy has missing or undeclared fields")
    if policy["schema"] != "sponsor-submission-transport-policy/v1":
        raise SubmissionTransportInputError("unsupported transport policy schema")
    policy_id = _token(policy["policy_id"], "policy_id")
    if policy["canonical_source_url"] != packet["canonical_source_url"]:
        raise SubmissionTransportInputError("policy canonical source does not match packet")
    if policy["packet_sha256"] != packet["packet_sha256"]:
        raise SubmissionTransportInputError("policy packet binding does not match packet")
    evidence_sha = _sha256_text(policy["policy_evidence_sha256"], "policy_evidence_sha256")

    raw_routes = policy["routes"]
    if type(raw_routes) is not list or not raw_routes or len(raw_routes) > _MAX_ROUTES:
        raise SubmissionTransportInputError("routes must be a non-empty bounded list")
    route_ids = set()
    routes = []
    for raw in raw_routes:
        if type(raw) is not dict or set(raw) != _ROUTE_KEYS:
            raise SubmissionTransportInputError("route has missing or undeclared fields")
        route_id = _token(raw["route_id"], "route_id")
        route_class = _token(raw["route_class"], "route_class")
        if route_id in route_ids:
            raise SubmissionTransportInputError("route IDs must be unique")
        route_ids.add(route_id)
        route_evidence = _sha256_text(raw["route_evidence_sha256"], "route_evidence_sha256")
        raw_failures = raw["failover_on"]
        if type(raw_failures) is not list or len(raw_failures) > 64:
            raise SubmissionTransportInputError("failover_on must be a bounded list")
        failures = [_failure(item, "failover_on entry") for item in raw_failures]
        if len(failures) != len(set(failures)):
            raise SubmissionTransportInputError("failover_on contains duplicate failure classes")
        routes.append(
            {
                "route_id": route_id,
                "route_class": route_class,
                "route_evidence_sha256": route_evidence,
                "failover_on": failures,
            }
        )

    supplied_policy_sha = _sha256_text(policy["policy_sha256"], "policy_sha256")
    core = dict(policy)
    del core["policy_sha256"]
    if _sha256(core) != supplied_policy_sha:
        raise SubmissionTransportInputError("policy_sha256 does not match canonical policy bytes")

    return {
        "policy_id": policy_id,
        "policy_sha256": supplied_policy_sha,
        "policy_evidence_sha256": evidence_sha,
        "routes": routes,
    }


def _check_attempts(attempts: Any, packet: dict[str, Any], routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if type(attempts) is not list or len(attempts) > _MAX_ATTEMPTS:
        raise SubmissionTransportInputError("attempts must be a bounded list")
    if len(attempts) > len(routes):
        raise SubmissionTransportInputError("attempts exceed policy route count")

    checked = []
    seen_attempt_ids = set()
    for index, raw in enumerate(attempts):
        if type(raw) is not dict or set(raw) != _ATTEMPT_KEYS:
            raise SubmissionTransportInputError("attempt has missing or undeclared fields")
        attempt_id = _token(raw["attempt_id"], "attempt_id")
        if attempt_id in seen_attempt_ids:
            raise SubmissionTransportInputError("attempt IDs must be unique")
        seen_attempt_ids.add(attempt_id)

        expected_route = routes[index]
        route_id = _token(raw["route_id"], "attempt route_id")
        if route_id != expected_route["route_id"]:
            raise SubmissionTransportInputError("attempts must be an exact route-policy prefix; route skipping is forbidden")
        if raw["packet_sha256"] != packet["packet_sha256"]:
            raise SubmissionTransportInputError("attempt packet binding does not match")
        if raw["head_sha"] != packet["head_sha"]:
            raise SubmissionTransportInputError("attempt head binding does not match")
        if raw["artifact_evidence_sha256"] != packet["artifact_evidence_sha256"]:
            raise SubmissionTransportInputError("attempt artifact evidence binding does not match")
        provider_evidence_sha = _sha256_text(
            raw["provider_evidence_sha256"], "provider_evidence_sha256"
        )
        outcome = raw["outcome"]
        if outcome not in _ALLOWED_OUTCOMES:
            raise SubmissionTransportInputError("attempt outcome is unsupported")
        failure_class = raw["failure_class"]
        if outcome == "FAILED_CONFIRMED":
            failure_class = _failure(failure_class, "failure_class")
        elif failure_class is not None:
            raise SubmissionTransportInputError("failure_class must be null unless outcome is FAILED_CONFIRMED")

        checked.append(
            {
                "attempt_id": attempt_id,
                "route_id": route_id,
                "outcome": outcome,
                "failure_class": failure_class,
                "provider_evidence_sha256": provider_evidence_sha,
            }
        )

    # A success or ambiguous result is a stop boundary.  A confirmed failure may
    # advance only when the exact failure class was pre-authorized by that route.
    for index, attempt in enumerate(checked[:-1]):
        route = routes[index]
        if attempt["outcome"] in {"SUCCESS_CONFIRMED", "AMBIGUOUS"}:
            raise SubmissionTransportInputError("attempts continue after a stop-boundary outcome")
        if attempt["failure_class"] not in route["failover_on"]:
            raise SubmissionTransportInputError("attempts advance after an unapproved failure class")

    return checked


def compile_transport_decision(request: Any) -> dict[str, Any]:
    """Compile one deterministic, send-free transport decision receipt."""
    if type(request) is not dict or set(request) != {"packet", "policy", "attempts"}:
        raise SubmissionTransportInputError("request must contain exactly packet, policy, and attempts")

    packet = _check_packet(request["packet"])
    policy = _check_policy(request["policy"], packet)
    routes = policy["routes"]
    attempts = _check_attempts(request["attempts"], packet, routes)

    next_route = None
    reason_codes = []
    if not attempts:
        disposition = "READY_PRIMARY"
        next_route = routes[0]
        reason_codes = ["PRIMARY_ROUTE_NOT_ATTEMPTED"]
    else:
        last_index = len(attempts) - 1
        last = attempts[-1]
        route = routes[last_index]
        if last["outcome"] == "SUCCESS_CONFIRMED":
            disposition = "ALREADY_SUBMITTED"
            reason_codes = ["SUCCESS_CONFIRMED"]
        elif last["outcome"] == "AMBIGUOUS":
            disposition = "HOLD_AMBIGUOUS_PROVIDER_OUTCOME"
            reason_codes = ["PROVIDER_OUTCOME_AMBIGUOUS"]
        elif last["failure_class"] not in route["failover_on"]:
            disposition = "HOLD_FAILURE_NOT_AUTHORIZED_FOR_FAILOVER"
            reason_codes = ["FAILURE_CLASS_NOT_ALLOWLISTED"]
        elif len(attempts) >= len(routes):
            disposition = "HOLD_NO_AUTHORIZED_ROUTE_REMAINS"
            reason_codes = ["AUTHORIZED_ROUTES_EXHAUSTED"]
        else:
            disposition = "READY_FALLBACK"
            next_route = routes[len(attempts)]
            reason_codes = ["CONFIRMED_FAILURE_ALLOWLISTED", "NEXT_POLICY_ROUTE_AVAILABLE"]

    public_next = None
    if next_route is not None:
        public_next = {
            "route_id": next_route["route_id"],
            "route_class": next_route["route_class"],
            "route_evidence_sha256": next_route["route_evidence_sha256"],
        }

    receipt = {
        "schema": "submission-transport-decision/v1",
        "canonical_source_url": packet["canonical_source_url"],
        "packet_sha256": packet["packet_sha256"],
        "head_sha": packet["head_sha"],
        "artifact_evidence_sha256": packet["artifact_evidence_sha256"],
        "policy_id": policy["policy_id"],
        "policy_sha256": policy["policy_sha256"],
        "policy_evidence_sha256": policy["policy_evidence_sha256"],
        "attempt_count": len(attempts),
        "completed_attempts": attempts,
        "disposition": disposition,
        "reason_codes": reason_codes,
        "next_route": public_next,
        "authority": {
            "external_send_authorized": False,
            "global_outbound_lease_required": True,
            "sponsor_route_authenticity_inferred": False,
            "sponsor_acceptance_inferred": False,
            "payment_inferred": False,
            "cash_claim": False,
        },
    }
    receipt["decision_sha256"] = _sha256(receipt)
    return receipt


def verify_transport_decision(request: Any, receipt: Any) -> bool:
    """Return True only when ``receipt`` exactly recompiles from ``request``."""
    if type(receipt) is not dict:
        return False
    try:
        expected = compile_transport_decision(request)
    except SubmissionTransportInputError:
        return False
    return expected == receipt


def _no_duplicate_object(pairs: Iterable[Tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionTransportInputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json_loads(text: str) -> Any:
    """Load JSON while rejecting duplicate object keys and non-standard constants."""
    def reject_constant(value: str) -> None:
        raise SubmissionTransportInputError(f"non-finite JSON constant is forbidden: {value}")

    try:
        return json.loads(text, object_pairs_hook=_no_duplicate_object, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise SubmissionTransportInputError(f"invalid JSON: {exc.msg}") from exc


def _load_request(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    value = strict_json_loads(raw)
    if type(value) is not dict:
        raise SubmissionTransportInputError("request JSON must contain an object")
    return value


def format_summary(receipt: dict[str, Any]) -> str:
    route = receipt["next_route"]
    route_id = "none" if route is None else route["route_id"]
    return (
        f"disposition={receipt['disposition']} attempts={receipt['attempt_count']} "
        f"next_route={route_id} external_send_authorized=false "
        f"global_outbound_lease_required=true"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.submission_transport_router",
        description="Compile a send-free, evidence-bound submission transport decision.",
    )
    parser.add_argument("request", help="JSON request path, or - for stdin")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    try:
        request = _load_request(args.request)
        receipt = compile_transport_decision(request)
    except (OSError, SubmissionTransportInputError) as exc:
        parser.error(str(exc))
    if args.summary:
        print(format_summary(receipt))
    else:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["disposition"] in {"READY_PRIMARY", "READY_FALLBACK", "ALREADY_SUBMITTED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
