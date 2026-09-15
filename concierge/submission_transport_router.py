# SPDX-License-Identifier: MIT
"""Evidence-bound, send-free routing for bounty submission transport failover.

A caller-controlled string is never enough to prove a provider attempt failed.
Recorded outcomes are consumed only from a provider-attempt authority receipt that
is authenticated by a verifier supplied outside the routing request.  The
router itself never sends, signs provider receipts, or infers sponsor acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit


class SubmissionTransportInputError(ValueError):
    """Raised when transport evidence is structurally unsafe or inconsistent."""


AuthorityVerifier = Callable[[dict[str, Any]], bool]

_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_FAILURE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,127}$")
_GITHUB_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
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
_MAX_TEXT = 1024
_MAX_KEYRING_ENTRIES = 64
_MIN_AUTHORITY_KEY_BYTES = 32
_MAX_AUTHORITY_KEY_BYTES = 128

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
    if any(marker in value for marker in _AMBIGUOUS_FAILURE_MARKERS):
        raise SubmissionTransportInputError(
            f"{name} denotes ambiguous/non-terminal provider state; use AMBIGUOUS outcome"
        )
    return value


def _strict_issue_url(value: Any) -> str:
    value = _bounded_text(value, "canonical_source_url")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise SubmissionTransportInputError("canonical_source_url port is invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SubmissionTransportInputError("canonical_source_url must be canonical github.com HTTPS")
    parts = parsed.path.split("/")
    if len(parts) != 5 or parts[0] != "" or parts[3] != "issues":
        raise SubmissionTransportInputError("canonical_source_url has invalid issue path shape")
    owner, repo, raw_number = parts[1], parts[2], parts[4]
    if (
        not _GITHUB_NAME_RE.fullmatch(owner)
        or not _GITHUB_NAME_RE.fullmatch(repo)
        or owner in {".", ".."}
        or repo in {".", ".."}
        or not raw_number.isdigit()
        or int(raw_number) <= 0
        or str(int(raw_number)) != raw_number
    ):
        raise SubmissionTransportInputError("canonical_source_url repository/issue identity is invalid")
    canonical = f"https://github.com/{owner}/{repo}/issues/{raw_number}"
    if value != canonical:
        raise SubmissionTransportInputError("canonical_source_url must use canonical spelling")
    return canonical


def _check_packet(packet: Any) -> dict[str, Any]:
    if type(packet) is not dict or set(packet) != _PACKET_KEYS:
        raise SubmissionTransportInputError("packet must be one exact bounty-submission-packet/v1 inner packet")
    if packet["disposition"] != "READY_FOR_HUMAN_SUBMISSION":
        raise SubmissionTransportInputError("packet is not READY_FOR_HUMAN_SUBMISSION")
    if packet["reason_codes"] != []:
        raise SubmissionTransportInputError("ready packet must have no reason codes")
    source = _strict_issue_url(packet["canonical_source_url"])

    authority = packet["authority"]
    if type(authority) is not dict:
        raise SubmissionTransportInputError("packet authority is missing")
    if (
        authority.get("submission") != "human_only"
        or authority.get("reward") != "advertised_only"
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
    """Return the exact operation descriptor a trusted provider adapter must bind.

    This helper does not authenticate or perform an external operation.  It gives
    the provider adapter a stable operation digest to include in its separately
    authenticated post-attempt receipt.
    """
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
    authority_verifier: Optional[AuthorityVerifier],
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
        verified = False
        if authority_verifier is not None:
            try:
                result = authority_verifier(dict(receipt))
            except Exception as exc:
                raise SubmissionTransportInputError("provider authority verifier failed closed") from exc
            if type(result) is not bool:
                raise SubmissionTransportInputError("provider authority verifier must return bool")
            verified = result
        checked.append({"receipt": receipt, "authority_verified": verified})

    for index, item in enumerate(checked[:-1]):
        receipt = item["receipt"]
        if not item["authority_verified"]:
            raise SubmissionTransportInputError("attempts continue after unverified provider authority")
        if receipt["outcome"] in {"SUCCESS_CONFIRMED", "AMBIGUOUS"}:
            raise SubmissionTransportInputError("attempts continue after a stop-boundary outcome")
        if receipt["failure_class"] not in routes[index]["failover_on"]:
            raise SubmissionTransportInputError("attempts advance after an unapproved failure class")

    return checked


def make_hmac_authority_verifier(keys: Mapping[str, bytes]) -> AuthorityVerifier:
    """Build a verifier from host-provisioned HMAC keys kept outside requests.

    This is a concrete stdlib reference boundary for integrations.  Provider
    adapters should own signing keys; orchestration request JSON must never carry
    them.  The returned verifier only authenticates receipts and does not sign.
    """
    if not isinstance(keys, Mapping) or len(keys) > _MAX_KEYRING_ENTRIES:
        raise SubmissionTransportInputError("authority keyring must be a bounded mapping")
    frozen: dict[str, bytes] = {}
    for raw_id, raw_key in keys.items():
        authority_id = _token(raw_id, "authority keyring id")
        if type(raw_key) is not bytes or not (_MIN_AUTHORITY_KEY_BYTES <= len(raw_key) <= _MAX_AUTHORITY_KEY_BYTES):
            raise SubmissionTransportInputError("authority verification keys must be 32..128 raw bytes")
        frozen[authority_id] = bytes(raw_key)

    def verify(receipt: dict[str, Any]) -> bool:
        authority_id = receipt.get("authority_id")
        key = frozen.get(authority_id)
        if key is None:
            return False
        supplied = receipt.get("auth_tag_hmac_sha256")
        if type(supplied) is not str or not _SHA256_RE.fullmatch(supplied):
            return False
        body = dict(receipt)
        del body["auth_tag_hmac_sha256"]
        expected = hmac.new(key, _canonical_bytes(body), hashlib.sha256).hexdigest()
        return hmac.compare_digest(supplied, expected)

    return verify


def compile_transport_decision(
    request: Any, *, authority_verifier: Optional[AuthorityVerifier] = None
) -> dict[str, Any]:
    """Compile one deterministic, send-free transport decision receipt.

    Any recorded provider attempt is untrusted unless ``authority_verifier``
    independently authenticates its provider-attempt receipt.  Unverified
    provider history can never unlock a fallback route.
    """
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

    public_attempts = [
        _public_attempt(item["receipt"], item["authority_verified"]) for item in attempts
    ]
    receipt = {
        "schema": "submission-transport-decision/v2",
        "canonical_source_url": packet["canonical_source_url"],
        "packet_sha256": packet["packet_sha256"],
        "head_sha": packet["head_sha"],
        "artifact_evidence_sha256": packet["artifact_evidence_sha256"],
        "policy_id": policy["policy_id"],
        "policy_sha256": policy["policy_sha256"],
        "policy_evidence_sha256": policy["policy_evidence_sha256"],
        "attempt_count": len(attempts),
        "completed_attempts": public_attempts,
        "disposition": disposition,
        "reason_codes": reason_codes,
        "next_route": public_next,
        "authority": {
            "provider_outcome_authority_required": bool(attempts),
            "all_recorded_outcomes_authority_verified": all(
                item["authority_verified"] for item in attempts
            ),
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


def verify_transport_decision(
    request: Any, receipt: Any, *, authority_verifier: Optional[AuthorityVerifier] = None
) -> bool:
    """Return True only when ``receipt`` exactly recompiles from ``request``."""
    if type(receipt) is not dict:
        return False
    try:
        expected = compile_transport_decision(request, authority_verifier=authority_verifier)
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


def _load_json(path: str) -> Any:
    if path == "-":
        import sys

        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    return strict_json_loads(raw)


def _load_keyring(path: Optional[str]) -> Optional[AuthorityVerifier]:
    if path is None:
        return None
    value = _load_json(path)
    if type(value) is not dict or len(value) > _MAX_KEYRING_ENTRIES:
        raise SubmissionTransportInputError("authority keyring file must contain one bounded JSON object")
    keys: dict[str, bytes] = {}
    for raw_id, raw_hex in value.items():
        authority_id = _token(raw_id, "authority keyring id")
        if type(raw_hex) is not str or len(raw_hex) % 2 or not re.fullmatch(r"[0-9a-f]+", raw_hex):
            raise SubmissionTransportInputError("authority keyring values must be lowercase hex")
        try:
            raw_key = bytes.fromhex(raw_hex)
        except ValueError as exc:
            raise SubmissionTransportInputError("authority keyring contains invalid hex") from exc
        keys[authority_id] = raw_key
    return make_hmac_authority_verifier(keys)


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
        description="Compile a send-free, authority-verified submission transport decision.",
    )
    parser.add_argument("request", help="JSON request path, or - for stdin")
    parser.add_argument(
        "--authority-keyring",
        help="separately provisioned JSON mapping authority_id to 32..128-byte lowercase-hex HMAC key",
    )
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    try:
        request = _load_json(args.request)
        if type(request) is not dict:
            raise SubmissionTransportInputError("request JSON must contain an object")
        verifier = _load_keyring(args.authority_keyring)
        receipt = compile_transport_decision(request, authority_verifier=verifier)
    except (OSError, SubmissionTransportInputError) as exc:
        parser.error(str(exc))
    if args.summary:
        print(format_summary(receipt))
    else:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["disposition"] in {"READY_PRIMARY", "READY_FALLBACK", "ALREADY_SUBMITTED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
