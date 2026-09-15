import copy
import hashlib
import json

import pytest

from concierge.submission_transport_router import (
    SubmissionTransportInputError,
    compile_transport_decision,
    strict_json_loads,
    verify_transport_decision,
)


HEX40 = "a" * 40
EVIDENCE = "b" * 64
POLICY_EVIDENCE = "c" * 64
ROUTE_GH_EVIDENCE = "d" * 64
ROUTE_EMAIL_EVIDENCE = "e" * 64
PROVIDER_EVIDENCE = "f" * 64
PROVIDER_EVIDENCE_2 = "1" * 64
SOURCE = "https://github.com/example/project/issues/315"


def check(condition, message="check failed"):
    if not condition:
        raise AssertionError(message)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def ready_packet():
    packet = {
        "canonical_source_url": SOURCE,
        "advertised_reward_usd": "90",
        "disposition": "READY_FOR_HUMAN_SUBMISSION",
        "reason_codes": [],
        "evidence": {
            "pull_request_url": "https://github.com/example/project/pull/400",
            "pull_request_repo": "example/project",
            "pull_request_number": 400,
            "head_sha": HEX40,
            "changed_paths": ["src/fix.py"],
            "allowed_paths": ["src/fix.py"],
            "tests": [{"command": "pytest -q", "outcome": "PASS"}],
            "evidence_sha256": EVIDENCE,
            "acceptance_checks": [{"criterion_id": "tests", "status": "PASS"}],
        },
        "authority": {
            "submission": "human_only",
            "reward": "advertised_only",
            "acceptance": "not_inferred",
            "payout": "not_inferred",
            "cash_claim": False,
        },
    }
    packet["packet_sha256"] = digest(packet)
    return packet


def route_policy(packet, include_email=True):
    routes = [
        {
            "route_id": "upstream-github",
            "route_class": "github-pr",
            "route_evidence_sha256": ROUTE_GH_EVIDENCE,
            "failover_on": ["PROVIDER_403_INTEGRATION_FORBIDDEN"],
        }
    ]
    if include_email:
        routes.append(
            {
                "route_id": "sponsor-email",
                "route_class": "email",
                "route_evidence_sha256": ROUTE_EMAIL_EVIDENCE,
                "failover_on": ["PROVIDER_TERMINAL_REJECTED"],
            }
        )
    policy = {
        "schema": "sponsor-submission-transport-policy/v1",
        "policy_id": "rustchain-315-v1",
        "canonical_source_url": SOURCE,
        "packet_sha256": packet["packet_sha256"],
        "policy_evidence_sha256": POLICY_EVIDENCE,
        "routes": routes,
    }
    policy["policy_sha256"] = digest(policy)
    return policy


def attempt(packet, *, outcome, failure=None, route_id="upstream-github", evidence=PROVIDER_EVIDENCE):
    return {
        "attempt_id": "try-1" if route_id == "upstream-github" else "try-2",
        "route_id": route_id,
        "packet_sha256": packet["packet_sha256"],
        "head_sha": HEX40,
        "artifact_evidence_sha256": EVIDENCE,
        "outcome": outcome,
        "failure_class": failure,
        "provider_evidence_sha256": evidence,
    }


def request(*attempts, include_email=True):
    packet = ready_packet()
    return {
        "packet": packet,
        "policy": route_policy(packet, include_email=include_email),
        "attempts": list(attempts),
    }


def test_primary_route_is_selected_without_attempts():
    req = request()
    receipt = compile_transport_decision(req)
    check(receipt["disposition"] == "READY_PRIMARY")
    check(receipt["next_route"]["route_id"] == "upstream-github")
    check(receipt["authority"]["external_send_authorized"] is False)
    check(receipt["authority"]["global_outbound_lease_required"] is True)
    check(verify_transport_decision(req, receipt))


def test_confirmed_403_advances_to_exact_policy_fallback():
    packet = ready_packet()
    failed = attempt(
        packet,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    req = {"packet": packet, "policy": route_policy(packet), "attempts": [failed]}
    receipt = compile_transport_decision(req)
    check(receipt["disposition"] == "READY_FALLBACK")
    check(receipt["next_route"] == {
        "route_id": "sponsor-email",
        "route_class": "email",
        "route_evidence_sha256": ROUTE_EMAIL_EVIDENCE,
    })
    check(receipt["completed_attempts"][0]["provider_evidence_sha256"] == PROVIDER_EVIDENCE)


def test_success_is_terminal_and_never_selects_fallback():
    packet = ready_packet()
    successful = attempt(packet, outcome="SUCCESS_CONFIRMED")
    req = {"packet": packet, "policy": route_policy(packet), "attempts": [successful]}
    receipt = compile_transport_decision(req)
    check(receipt["disposition"] == "ALREADY_SUBMITTED")
    check(receipt["next_route"] is None)


def test_ambiguous_provider_outcome_holds_instead_of_double_send():
    packet = ready_packet()
    uncertain = attempt(packet, outcome="AMBIGUOUS")
    req = {"packet": packet, "policy": route_policy(packet), "attempts": [uncertain]}
    receipt = compile_transport_decision(req)
    check(receipt["disposition"] == "HOLD_AMBIGUOUS_PROVIDER_OUTCOME")
    check(receipt["next_route"] is None)


def test_attempt_after_ambiguous_outcome_is_rejected():
    packet = ready_packet()
    uncertain = attempt(packet, outcome="AMBIGUOUS")
    email = attempt(
        packet,
        outcome="SUCCESS_CONFIRMED",
        route_id="sponsor-email",
        evidence=PROVIDER_EVIDENCE_2,
    )
    req = {"packet": packet, "policy": route_policy(packet), "attempts": [uncertain, email]}
    with pytest.raises(SubmissionTransportInputError, match="stop-boundary"):
        compile_transport_decision(req)


def test_unallowlisted_terminal_failure_holds():
    packet = ready_packet()
    failed = attempt(packet, outcome="FAILED_CONFIRMED", failure="PROVIDER_TERMINAL_POLICY_REJECTED")
    req = {"packet": packet, "policy": route_policy(packet), "attempts": [failed]}
    receipt = compile_transport_decision(req)
    check(receipt["disposition"] == "HOLD_FAILURE_NOT_AUTHORIZED_FOR_FAILOVER")
    check(receipt["next_route"] is None)


def test_ambiguous_like_failure_class_must_use_ambiguous_outcome():
    packet = ready_packet()
    failed = attempt(packet, outcome="FAILED_CONFIRMED", failure="PROVIDER_TIMEOUT_CONFIRMED")
    req = {"packet": packet, "policy": route_policy(packet), "attempts": [failed]}
    with pytest.raises(SubmissionTransportInputError, match="ambiguous/non-terminal"):
        compile_transport_decision(req)


def test_advancing_after_unallowlisted_failure_is_rejected():
    packet = ready_packet()
    failed = attempt(packet, outcome="FAILED_CONFIRMED", failure="PROVIDER_TERMINAL_POLICY_REJECTED")
    email = attempt(
        packet,
        outcome="SUCCESS_CONFIRMED",
        route_id="sponsor-email",
        evidence=PROVIDER_EVIDENCE_2,
    )
    req = {"packet": packet, "policy": route_policy(packet), "attempts": [failed, email]}
    with pytest.raises(SubmissionTransportInputError, match="unapproved failure"):
        compile_transport_decision(req)


def test_exhausted_policy_holds():
    packet = ready_packet()
    failed = attempt(
        packet,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    req = {"packet": packet, "policy": route_policy(packet, include_email=False), "attempts": [failed]}
    receipt = compile_transport_decision(req)
    check(receipt["disposition"] == "HOLD_NO_AUTHORIZED_ROUTE_REMAINS")


def test_route_skipping_is_rejected():
    packet = ready_packet()
    email = attempt(packet, outcome="SUCCESS_CONFIRMED", route_id="sponsor-email")
    req = {"packet": packet, "policy": route_policy(packet), "attempts": [email]}
    with pytest.raises(SubmissionTransportInputError, match="route skipping"):
        compile_transport_decision(req)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("packet_sha256", "9" * 64),
        ("head_sha", "9" * 40),
        ("artifact_evidence_sha256", "9" * 64),
    ],
)
def test_cross_artifact_attempt_bindings_are_rejected(field, bad):
    packet = ready_packet()
    failed = attempt(
        packet,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    failed[field] = bad
    req = {"packet": packet, "policy": route_policy(packet), "attempts": [failed]}
    with pytest.raises(SubmissionTransportInputError, match="binding does not match"):
        compile_transport_decision(req)


def test_duplicate_route_ids_are_rejected():
    packet = ready_packet()
    policy = route_policy(packet)
    policy["routes"][1]["route_id"] = policy["routes"][0]["route_id"]
    policy["policy_sha256"] = digest({k: v for k, v in policy.items() if k != "policy_sha256"})
    with pytest.raises(SubmissionTransportInputError, match="route IDs must be unique"):
        compile_transport_decision({"packet": packet, "policy": policy, "attempts": []})


def test_packet_digest_tamper_is_rejected():
    packet = ready_packet()
    packet["advertised_reward_usd"] = "9000"
    with pytest.raises(SubmissionTransportInputError, match="packet_sha256"):
        compile_transport_decision({"packet": packet, "policy": route_policy(ready_packet()), "attempts": []})


def test_policy_digest_tamper_is_rejected():
    packet = ready_packet()
    policy = route_policy(packet)
    policy["routes"][0]["failover_on"].append("PROVIDER_404_NOT_FOUND")
    with pytest.raises(SubmissionTransportInputError, match="policy_sha256"):
        compile_transport_decision({"packet": packet, "policy": policy, "attempts": []})


def test_nonready_packet_is_rejected():
    packet = ready_packet()
    packet["disposition"] = "HOLD"
    packet["reason_codes"] = ["TESTS_NOT_ALL_PASS"]
    packet["packet_sha256"] = digest({k: v for k, v in packet.items() if k != "packet_sha256"})
    with pytest.raises(SubmissionTransportInputError, match="not READY"):
        compile_transport_decision({"packet": packet, "policy": route_policy(ready_packet()), "attempts": []})


@pytest.mark.parametrize(
    "bad_source",
    [
        "http://github.com/example/project/issues/315",
        "https://user@github.com/example/project/issues/315",
        "https://github.com/example/project/issues/315?route=email",
        "https://github.com/example/project/issues/0315",
        "https://github.com/example/project/issues/315/extra",
    ],
)
def test_noncanonical_source_urls_are_rejected(bad_source):
    packet = ready_packet()
    packet["canonical_source_url"] = bad_source
    packet["packet_sha256"] = digest({k: v for k, v in packet.items() if k != "packet_sha256"})
    policy = route_policy(ready_packet())
    policy["canonical_source_url"] = bad_source
    policy["packet_sha256"] = packet["packet_sha256"]
    policy["policy_sha256"] = digest({k: v for k, v in policy.items() if k != "policy_sha256"})
    with pytest.raises(SubmissionTransportInputError, match="canonical_source_url"):
        compile_transport_decision({"packet": packet, "policy": policy, "attempts": []})


def test_packet_reward_authority_must_remain_advertised_only():
    packet = ready_packet()
    packet["authority"]["reward"] = "earned"
    packet["packet_sha256"] = digest({k: v for k, v in packet.items() if k != "packet_sha256"})
    policy = route_policy(packet)
    with pytest.raises(SubmissionTransportInputError, match="authority"):
        compile_transport_decision({"packet": packet, "policy": policy, "attempts": []})


def test_strict_json_rejects_duplicate_keys_and_nonfinite_constants():
    with pytest.raises(SubmissionTransportInputError, match="duplicate JSON key"):
        strict_json_loads('{"packet":{},"packet":{}}')
    with pytest.raises(SubmissionTransportInputError, match="non-finite"):
        strict_json_loads('{"x":NaN}')


def test_receipt_verifier_rejects_mutation():
    req = request()
    receipt = compile_transport_decision(req)
    tampered = copy.deepcopy(receipt)
    tampered["next_route"]["route_id"] = "attacker-route"
    check(not verify_transport_decision(req, tampered))


def test_receipt_exposes_no_route_address_or_send_authority():
    req = request()
    receipt = compile_transport_decision(req)
    rendered = json.dumps(receipt, sort_keys=True)
    check("@example" not in rendered)
    check("mailto:" not in rendered)
    check(receipt["authority"] == {
        "external_send_authorized": False,
        "global_outbound_lease_required": True,
        "sponsor_route_authenticity_inferred": False,
        "sponsor_acceptance_inferred": False,
        "payment_inferred": False,
        "cash_claim": False,
    })
    core = dict(receipt)
    supplied = core.pop("decision_sha256")
    check(supplied == digest(core))
