# SPDX-License-Identifier: MIT
"""Production-safe, send-free routing for bounty submission transport failover.

Every production entrypoint in this module validates submission packets through
``submission_custody._verify_submission_packet`` and authenticates provider
attempts only by exact receipt equality against one fixed, root-owned host
ledger.  No production library or CLI surface accepts a verifier, keyring,
ledger path, environment selector, or caller-authored provider outcome.

A private test-observation helper can inject a verifier to exercise the pure
route algorithm.  It deliberately emits a non-production schema, no next route,
and ``HOLD_TEST_ONLY_AUTHORITY`` so injected authority can never be confused
with a production decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Tuple

from concierge.submission_custody import SubmissionCustodyError, _verify_submission_packet


class SubmissionTransportInputError(ValueError):
    """Raised when transport evidence is malformed, unsafe, or inconsistent."""


_AuthorityVerifier = Callable[[dict[str, Any]], bool]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_FAILURE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,127}$")
_AMBIGUOUS_FAILURE_MARKERS = (
    "TIMEOUT",
    "UNKNOWN",
    "NETWORK",
    "CONNECTION",
    "RATE_LIMIT",
    "RETRY",
    "TEMPORARY",
    "TRANSIENT",
    "UNAVAILABLE",
    "NO_RECEIPT",
)
_MAX_ROUTES = 16
_MAX_ATTEMPTS = 16
_MAX_HOST_RECEIPT_BYTES = 32 * 1024

_POLICY_KEYS = {
    "schema",
    "policy_id",
    "canonical_source_url",
    "packet_sha256",
    "policy_evidence_sha256",
    "routes",
    "policy_sha256",
}
_ROUTE_KEYS = {
    "route_id",
    "route_class",
    "authority_id",
    "route_evidence_sha256",
    "failover_on",
}
_RECEIPT_KEYS = {
    "schema",
    "authority_id",
    "operation_sha256",
    "attempt_id",
    "route_id",
    "packet_sha256",
    "head_sha",
    "artifact_evidence_sha256",
    "policy_sha256",
    "outcome",
    "failure_class",
    "provider_evidence_sha256",
    "auth_tag_hmac_sha256",
}
_ALLOWED_OUTCOMES = {"FAILED_CONFIRMED", "SUCCESS_CONFIRMED", "AMBIGUOUS"}
_PACKET_ERROR_MESSAGES = {
    "PACKET_DIGEST_MISMATCH": "packet_sha256 does not match canonical packet bytes",
    "PACKET_NOT_READY": "packet is not READY_FOR_HUMAN_SUBMISSION",
    "SOURCE_URL_INVALID": "canonical_source_url is invalid",
    "PACKET_AUTHORITY_MISMATCH": "packet authority is invalid",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


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
    if any(marker in value for marker in _AMBIGUOUS_FAILURE_MARKERS):
        raise SubmissionTransportInputError(
            f"{name} denotes ambiguous/non-terminal provider state; use AMBIGUOUS outcome"
        )
    return value


def _check_packet(packet: Any) -> dict[str, Any]:
    """Use the repository's single canonical submission-packet verifier."""
    try:
        verified = _verify_submission_packet(packet)
    except SubmissionCustodyError as exc:
        detail = _PACKET_ERROR_MESSAGES.get(exc.code, "submission packet rejected")
        raise SubmissionTransportInputError(f"{detail} [{exc.code}]") from exc
    return {
        "canonical_source_url": verified["source_url"],
        "packet_sha256": verified["packet_sha256"],
        "head_sha": verified["head_sha"],
        "artifact_evidence_sha256": verified["artifact_digest"],
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
        authority_id = _token(raw["authority_id"], "authority_id")
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
                "authority_id": authority_id,
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


def _operation_descriptor(
    packet: dict[str, Any], policy: dict[str, Any], route: dict[str, Any], attempt_index: int
) -> dict[str, Any]:
    core = {
        "schema": "submission-transport-operation/v1",
        "attempt_index": attempt_index,
        "canonical_source_url": packet["canonical_source_url"],
        "packet_sha256": packet["packet_sha256"],
        "head_sha": packet["head_sha"],
        "artifact_evidence_sha256": packet["artifact_evidence_sha256"],
        "policy_sha256": policy["policy_sha256"],
        "route_id": route["route_id"],
        "route_class": route["route_class"],
        "authority_id": route["authority_id"],
        "route_evidence_sha256": route["route_evidence_sha256"],
    }
    return {**core, "operation_sha256": _sha256(core)}


def compile_transport_operation(packet: Any, policy: Any, attempt_index: int) -> dict[str, Any]:
    """Return the exact operation identity a trusted provider adapter must bind."""
    if type(attempt_index) is not int or attempt_index < 0:
        raise SubmissionTransportInputError("attempt_index must be a non-negative integer")
    checked_packet = _check_packet(packet)
    checked_policy = _check_policy(policy, checked_packet)
    routes = checked_policy["routes"]
    if attempt_index >= len(routes):
        raise SubmissionTransportInputError("attempt_index exceeds policy route count")
    return _operation_descriptor(checked_packet, checked_policy, routes[attempt_index], attempt_index)


def _public_attempt(receipt: dict[str, Any], verified: bool) -> dict[str, Any]:
    public = {
        "attempt_id": receipt["attempt_id"],
        "route_id": receipt["route_id"],
        "authority_id": receipt["authority_id"],
        "operation_sha256": receipt["operation_sha256"],
        "authority_receipt_sha256": _sha256(receipt),
        "authority_verified": verified,
    }
    if verified:
        public.update(
            {
                "outcome": receipt["outcome"],
                "failure_class": receipt["failure_class"],
                "provider_evidence_sha256": receipt["provider_evidence_sha256"],
            }
        )
    return public


def _check_receipt_shape(
    raw: Any,
    packet: dict[str, Any],
    policy: dict[str, Any],
    route: dict[str, Any],
    attempt_index: int,
) -> dict[str, Any]:
    if type(raw) is not dict or set(raw) != _RECEIPT_KEYS:
        raise SubmissionTransportInputError("provider authority receipt has missing or undeclared fields")
    if raw["schema"] != "provider-attempt-authority/v1":
        raise SubmissionTransportInputError("unsupported provider authority receipt schema")
    authority_id = _token(raw["authority_id"], "receipt authority_id")
    if authority_id != route["authority_id"]:
        raise SubmissionTransportInputError("receipt authority does not match policy route authority")
    attempt_id = _token(raw["attempt_id"], "attempt_id")
    route_id = _token(raw["route_id"], "receipt route_id")
    if route_id != route["route_id"]:
        raise SubmissionTransportInputError("receipt route does not match exact policy prefix")
    if raw["packet_sha256"] != packet["packet_sha256"]:
        raise SubmissionTransportInputError("receipt packet binding does not match")
    if raw["head_sha"] != packet["head_sha"]:
        raise SubmissionTransportInputError("receipt head binding does not match")
    if raw["artifact_evidence_sha256"] != packet["artifact_evidence_sha256"]:
        raise SubmissionTransportInputError("receipt artifact evidence binding does not match")
    if raw["policy_sha256"] != policy["policy_sha256"]:
        raise SubmissionTransportInputError("receipt policy binding does not match")
    expected_operation = _operation_descriptor(packet, policy, route, attempt_index)["operation_sha256"]
    if raw["operation_sha256"] != expected_operation:
        raise SubmissionTransportInputError("receipt operation binding does not match")

    provider_evidence_sha = _sha256_text(raw["provider_evidence_sha256"], "provider_evidence_sha256")
    auth_tag = _sha256_text(raw["auth_tag_hmac_sha256"], "auth_tag_hmac_sha256")
    outcome = raw["outcome"]
    if outcome not in _ALLOWED_OUTCOMES:
        raise SubmissionTransportInputError("provider authority outcome is unsupported")
    failure_class = raw["failure_class"]
    if outcome == "FAILED_CONFIRMED":
        failure_class = _failure(failure_class, "failure_class")
    elif failure_class is not None:
        raise SubmissionTransportInputError("failure_class must be null unless outcome is FAILED_CONFIRMED")

    return {
        "schema": "provider-attempt-authority/v1",
        "authority_id": authority_id,
        "operation_sha256": expected_operation,
        "attempt_id": attempt_id,
        "route_id": route_id,
        "packet_sha256": packet["packet_sha256"],
        "head_sha": packet["head_sha"],
        "artifact_evidence_sha256": packet["artifact_evidence_sha256"],
        "policy_sha256": policy["policy_sha256"],
        "outcome": outcome,
        "failure_class": failure_class,
        "provider_evidence_sha256": provider_evidence_sha,
        "auth_tag_hmac_sha256": auth_tag,
    }


def _check_attempts(
    attempts: Any,
    packet: dict[str, Any],
    policy: dict[str, Any],
    authority_verifier: _AuthorityVerifier,
) -> list[dict[str, Any]]:
    routes = policy["routes"]
    if type(attempts) is not list or len(attempts) > _MAX_ATTEMPTS:
        raise SubmissionTransportInputError("attempts must be a bounded list")
    if len(attempts) > len(routes):
        raise SubmissionTransportInputError("attempts exceed policy route count")

    checked = []
    seen_attempt_ids = set()
    for index, raw in enumerate(attempts):
        receipt = _check_receipt_shape(raw, packet, policy, routes[index], index)
        if receipt["attempt_id"] in seen_attempt_ids:
            raise SubmissionTransportInputError("attempt IDs must be unique")
        seen_attempt_ids.add(receipt["attempt_id"])
        try:
            result = authority_verifier(dict(receipt))
        except Exception as exc:
            raise SubmissionTransportInputError("provider authority verifier failed closed") from exc
        if type(result) is not bool:
            raise SubmissionTransportInputError("provider authority verifier must return bool")
        checked.append({"receipt": receipt, "authority_verified": result})

    for index, item in enumerate(checked[:-1]):
        receipt = item["receipt"]
        if not item["authority_verified"]:
            raise SubmissionTransportInputError("attempts continue after unverified provider authority")
        if receipt["outcome"] in {"SUCCESS_CONFIRMED", "AMBIGUOUS"}:
            raise SubmissionTransportInputError("attempts continue after a stop-boundary outcome")
        if receipt["failure_class"] not in routes[index]["failover_on"]:
            raise SubmissionTransportInputError("attempts advance after an unapproved failure class")

    return checked


def _read_host_receipt_from_directory(
    directory: Path, operation_sha256: str
) -> Optional[dict[str, Any]]:
    """Read one retained receipt without following directory or file symlinks.

    ``directory`` is parameterized only so isolated tests can exercise the file
    reader. Production compilation always calls this with the fixed literal host-ledger
    path embedded in ``_host_receipt_authority_verifier`` and exposes no selector.
    """
    if type(operation_sha256) is not str or not _SHA256_RE.fullmatch(operation_sha256):
        return None
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        return None

    dir_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        dir_flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        dir_flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        dir_flags |= os.O_NOFOLLOW
    try:
        dir_fd = os.open(directory, dir_flags)
    except OSError:
        return None
    try:
        try:
            dir_stat = os.fstat(dir_fd)
        except OSError:
            return None
        if (
            not stat.S_ISDIR(dir_stat.st_mode)
            or dir_stat.st_uid != 0
            or dir_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            return None

        file_flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            file_flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        try:
            file_fd = os.open(
                f"{operation_sha256}.json", file_flags, dir_fd=dir_fd
            )
        except OSError:
            return None
        try:
            try:
                file_stat = os.fstat(file_fd)
            except OSError:
                return None
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_uid != 0
                or file_stat.st_nlink != 1
                or file_stat.st_size <= 0
                or file_stat.st_size > _MAX_HOST_RECEIPT_BYTES
                or file_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_IROTH)
            ):
                return None
            chunks = []
            remaining = file_stat.st_size
            while remaining:
                chunk = os.read(file_fd, remaining)
                if not chunk:
                    return None
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(file_fd)
    finally:
        os.close(dir_fd)

    try:
        value = strict_json_loads(raw.decode("utf-8"))
    except (UnicodeError, SubmissionTransportInputError):
        return None
    return value if type(value) is dict else None


def _host_receipt_authority_verifier(receipt: dict[str, Any]) -> bool:
    # Fixed literal trust root: request JSON, CLI arguments, environment variables,
    # and public function parameters cannot select or replace this directory.
    retained = _read_host_receipt_from_directory(
        Path("/var/lib/bounty-concierge/provider-attempt-authority"),
        receipt.get("operation_sha256"),
    )
    return retained == receipt


def _compile_candidate(
    request: Any, *, authority_verifier: _AuthorityVerifier
) -> dict[str, Any]:
    if type(request) is not dict or set(request) != {"packet", "policy", "attempts"}:
        raise SubmissionTransportInputError("request must contain exactly packet, policy, and attempts")

    packet = _check_packet(request["packet"])
    policy = _check_policy(request["policy"], packet)
    routes = policy["routes"]
    attempts = _check_attempts(request["attempts"], packet, policy, authority_verifier)

    next_route = None
    reason_codes = []
    if not attempts:
        disposition = "READY_PRIMARY"
        next_route = routes[0]
        reason_codes = ["PRIMARY_ROUTE_NOT_ATTEMPTED"]
    else:
        last_index = len(attempts) - 1
        last_item = attempts[-1]
        last = last_item["receipt"]
        route = routes[last_index]
        if not last_item["authority_verified"]:
            disposition = "HOLD_PROVIDER_AUTHORITY_UNVERIFIED"
            reason_codes = ["PROVIDER_OUTCOME_AUTHORITY_UNVERIFIED"]
        elif last["outcome"] == "SUCCESS_CONFIRMED":
            disposition = "ALREADY_SUBMITTED"
            reason_codes = ["SUCCESS_CONFIRMED_BY_PROVIDER_AUTHORITY"]
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
            reason_codes = [
                "PROVIDER_FAILURE_AUTHORITY_VERIFIED",
                "CONFIRMED_FAILURE_ALLOWLISTED",
                "NEXT_POLICY_ROUTE_AVAILABLE",
            ]

    public_next = None
    if next_route is not None:
        public_next = {
            "route_id": next_route["route_id"],
            "route_class": next_route["route_class"],
            "authority_id": next_route["authority_id"],
            "route_evidence_sha256": next_route["route_evidence_sha256"],
        }

    return {
        "canonical_source_url": packet["canonical_source_url"],
        "packet_sha256": packet["packet_sha256"],
        "head_sha": packet["head_sha"],
        "artifact_evidence_sha256": packet["artifact_evidence_sha256"],
        "policy_id": policy["policy_id"],
        "policy_sha256": policy["policy_sha256"],
        "policy_evidence_sha256": policy["policy_evidence_sha256"],
        "attempt_count": len(attempts),
        "completed_attempts": [
            _public_attempt(item["receipt"], item["authority_verified"])
            for item in attempts
        ],
        "candidate_disposition": disposition,
        "candidate_reason_codes": reason_codes,
        "candidate_next_route": public_next,
        "all_recorded_outcomes_authority_verified": all(
            item["authority_verified"] for item in attempts
        ),
    }


def _production_receipt(candidate: dict[str, Any]) -> dict[str, Any]:
    receipt = {
        "schema": "submission-transport-decision/v2",
        "canonical_source_url": candidate["canonical_source_url"],
        "packet_sha256": candidate["packet_sha256"],
        "head_sha": candidate["head_sha"],
        "artifact_evidence_sha256": candidate["artifact_evidence_sha256"],
        "policy_id": candidate["policy_id"],
        "policy_sha256": candidate["policy_sha256"],
        "policy_evidence_sha256": candidate["policy_evidence_sha256"],
        "attempt_count": candidate["attempt_count"],
        "completed_attempts": candidate["completed_attempts"],
        "disposition": candidate["candidate_disposition"],
        "reason_codes": candidate["candidate_reason_codes"],
        "next_route": candidate["candidate_next_route"],
        "authority": {
            "provider_outcome_authority_required": bool(candidate["attempt_count"]),
            "all_recorded_outcomes_authority_verified": candidate[
                "all_recorded_outcomes_authority_verified"
            ],
            "provider_authority_trust_root": "fixed-root-owned-provider-receipt-ledger",
            "canonical_packet_verifier": "submission_custody._verify_submission_packet",
            "caller_verifier_injection_supported": False,
            "caller_keyring_selection_supported": False,
            "caller_trust_root_selection_supported": False,
            "direct_core_production_safe": True,
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


def compile_transport_decision(request: Any) -> dict[str, Any]:
    """Compile through the fixed host receipt ledger.

    The signature intentionally contains no verifier, keyring, path, or other
    authority selector. Direct import of this core and the public wrapper share
    this exact production path.
    """
    return _production_receipt(
        _compile_candidate(
            request, authority_verifier=_host_receipt_authority_verifier
        )
    )


def _compile_transport_test_observation(
    request: Any, *, authority_verifier: _AuthorityVerifier
) -> dict[str, Any]:
    """Exercise route mechanics without creating a production decision.

    This is an isolated-test seam only. Even a verifier that always returns
    True cannot produce a production schema, production decision hash, next
    route, READY disposition, or ALREADY_SUBMITTED disposition.
    """
    candidate = _compile_candidate(request, authority_verifier=authority_verifier)
    observed_route = candidate["candidate_next_route"]
    observation = {
        "schema": "submission-transport-test-observation/v1",
        "production_disposition": "HOLD_TEST_ONLY_AUTHORITY",
        "candidate_disposition": candidate["candidate_disposition"],
        "candidate_reason_codes": candidate["candidate_reason_codes"],
        "candidate_next_route_id": None
        if observed_route is None
        else observed_route["route_id"],
        "next_route": None,
        "production_authority_valid": False,
        "test_only_dependency_injection": True,
        "external_send_authorized": False,
    }
    observation["observation_sha256"] = _sha256(observation)
    return observation


def verify_transport_decision(request: Any, receipt: Any) -> bool:
    """Return True only when a production receipt exactly recompiles."""
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
    if type(text) is not str:
        raise SubmissionTransportInputError("JSON input must be text")

    def reject_constant(value: str) -> None:
        raise SubmissionTransportInputError(f"non-finite JSON constant is forbidden: {value}")

    try:
        return json.loads(
            text,
            object_pairs_hook=_no_duplicate_object,
            parse_constant=reject_constant,
        )
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
        prog="python -m concierge._submission_transport_router_core",
        description=(
            "Compile a send-free transport decision through the fixed root-owned "
            "provider receipt ledger."
        ),
    )
    parser.add_argument("request", help="JSON request path, or - for stdin")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = compile_transport_decision(_load_request(args.request))
    except (OSError, SubmissionTransportInputError) as exc:
        parser.error(str(exc))
    if args.summary:
        print(format_summary(receipt))
    else:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["disposition"] in {"READY_PRIMARY", "READY_FALLBACK", "ALREADY_SUBMITTED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
