import copy
import hashlib
import hmac
import json

import pytest

from concierge.submission_transport_router import (
    SubmissionTransportInputError,
    compile_transport_decision,
    compile_transport_operation,
    make_hmac_authority_verifier,
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
GH_AUTHORITY = "github-app-adapter-v1"
EMAIL_AUTHORITY = "smtp-adapter-v1"
GH_KEY = bytes.fromhex("11" * 32)
EMAIL_KEY = bytes.fromhex("22" * 32)
WRONG_KEY = bytes.fromhex("33" * 32)


def check(condition, message="check failed"):
    if not condition:
        raise AssertionError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


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
            "authority_id": GH_AUTHORITY,
            "route_evidence_sha256": ROUTE_GH_EVIDENCE,
            "failover_on": ["PROVIDER_403_INTEGRATION_FORBIDDEN"],
        }
    ]
    if include_email:
        routes.append(
            {
                "route_id": "sponsor-email",
                "route_class": "email",
                "authority_id": EMAIL_AUTHORITY,
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


def verifier(keys=None):
    return make_hmac_authority_verifier(
        keys
        or {
            GH_AUTHORITY: GH_KEY,
            EMAIL_AUTHORITY: EMAIL_KEY,
        }
    )


def sign_receipt(packet, policy, index, *, outcome, failure=None, evidence=None, key=None, attempt_id=None):
    operation = compile_transport_operation(packet, policy, index)
    route = policy["routes"][index]
    if evidence is None:
        evidence = PROVIDER_EVIDENCE if index == 0 else PROVIDER_EVIDENCE_2
    if key is None:
        key = GH_KEY if index == 0 else EMAIL_KEY
    receipt = {
        "schema": "provider-attempt-authority/v1",
        "authority_id": route["authority_id"],
        "operation_sha256": operation["operation_sha256"],
        "attempt_id": attempt_id or f"try-{index + 1}",
        "route_id": route["route_id"],
        "packet_sha256": packet["packet_sha256"],
        "head_sha": HEX40,
        "artifact_evidence_sha256": EVIDENCE,
        "policy_sha256": policy["policy_sha256"],
        "outcome": outcome,
        "failure_class": failure,
        "provider_evidence_sha256": evidence,
    }
    receipt["auth_tag_hmac_sha256"] = hmac.new(key, canonical(receipt), hashlib.sha256).hexdigest()
    return receipt


def request_with_receipts(receipts=None, include_email=True):
    packet = ready_packet()
    policy = route_policy(packet, include_email=include_email)
    return {"packet": packet, "policy": policy, "attempts": list(receipts or [])}


def test_primary_route_is_selected_without_attempts_and_without_verifier():
    req = request_with_receipts()
    receipt = compile_transport_decision(req)
    check(receipt["disposition"] == "READY_PRIMARY")
    check(receipt["next_route"]["route_id"] == "upstream-github")
    check(receipt["next_route"]["authority_id"] == GH_AUTHORITY)
    check(receipt["authority"]["external_send_authorized"] is False)
    check(receipt["authority"]["provider_outcome_authority_required"] is False)
    check(verify_transport_decision(req, receipt))


def test_authenticated_confirmed_403_advances_to_exact_policy_fallback():
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    req = {"packet": packet, "policy": policy, "attempts": [failed]}
    receipt = compile_transport_decision(req, authority_verifier=verifier())
    check(receipt["disposition"] == "READY_FALLBACK")
    check(receipt["next_route"] == {
        "route_id": "sponsor-email",
        "route_class": "email",
        "authority_id": EMAIL_AUTHORITY,
        "route_evidence_sha256": ROUTE_EMAIL_EVIDENCE,
    })
    check(receipt["completed_attempts"][0]["authority_verified"] is True)
    check(receipt["completed_attempts"][0]["outcome"] == "FAILED_CONFIRMED")
    check("PROVIDER_FAILURE_AUTHORITY_VERIFIED" in receipt["reason_codes"])


def test_correctly_signed_receipt_without_external_verifier_holds():
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    req = {"packet": packet, "policy": policy, "attempts": [failed]}
    receipt = compile_transport_decision(req)
    check(receipt["disposition"] == "HOLD_PROVIDER_AUTHORITY_UNVERIFIED")
    check(receipt["next_route"] is None)
    check(receipt["completed_attempts"][0]["authority_verified"] is False)
    check("outcome" not in receipt["completed_attempts"][0])
    check("failure_class" not in receipt["completed_attempts"][0])
    check("provider_evidence_sha256" not in receipt["completed_attempts"][0])


def test_arbitrary_digest_plus_forged_failed_confirmed_cannot_unlock_fallback():
    packet = ready_packet()
    policy = route_policy(packet)
    forged = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
        key=WRONG_KEY,
    )
    forged["provider_evidence_sha256"] = "9" * 64
    forged["auth_tag_hmac_sha256"] = "8" * 64
    req = {"packet": packet, "policy": policy, "attempts": [forged]}
    receipt = compile_transport_decision(req, authority_verifier=verifier())
    check(receipt["disposition"] == "HOLD_PROVIDER_AUTHORITY_UNVERIFIED")
    check(receipt["next_route"] is None)
    check(receipt["completed_attempts"][0]["authority_verified"] is False)


def test_wrong_trusted_key_holds_instead_of_laundering_claimed_outcome():
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    req = {"packet": packet, "policy": policy, "attempts": [failed]}
    bad_verifier = verifier({GH_AUTHORITY: WRONG_KEY, EMAIL_AUTHORITY: EMAIL_KEY})
    receipt = compile_transport_decision(req, authority_verifier=bad_verifier)
    check(receipt["disposition"] == "HOLD_PROVIDER_AUTHORITY_UNVERIFIED")
    check("outcome" not in receipt["completed_attempts"][0])


def test_authenticated_success_is_terminal_and_never_selects_fallback():
    packet = ready_packet()
    policy = route_policy(packet)
    successful = sign_receipt(packet, policy, 0, outcome="SUCCESS_CONFIRMED")
    req = {"packet": packet, "policy": policy, "attempts": [successful]}
    receipt = compile_transport_decision(req, authority_verifier=verifier())
    check(receipt["disposition"] == "ALREADY_SUBMITTED")
    check(receipt["next_route"] is None)


def test_authenticated_ambiguous_provider_outcome_holds_instead_of_double_send():
    packet = ready_packet()
    policy = route_policy(packet)
    uncertain = sign_receipt(packet, policy, 0, outcome="AMBIGUOUS")
    req = {"packet": packet, "policy": policy, "attempts": [uncertain]}
    receipt = compile_transport_decision(req, authority_verifier=verifier())
    check(receipt["disposition"] == "HOLD_AMBIGUOUS_PROVIDER_OUTCOME")
    check(receipt["next_route"] is None)


def test_attempt_after_ambiguous_outcome_is_rejected():
    packet = ready_packet()
    policy = route_policy(packet)
    uncertain = sign_receipt(packet, policy, 0, outcome="AMBIGUOUS")
    email = sign_receipt(packet, policy, 1, outcome="SUCCESS_CONFIRMED")
    req = {"packet": packet, "policy": policy, "attempts": [uncertain, email]}
    with pytest.raises(SubmissionTransportInputError, match="stop-boundary"):
        compile_transport_decision(req, authority_verifier=verifier())


def test_attempt_after_unverified_provider_authority_is_rejected():
    packet = ready_packet()
    policy = route_policy(packet)
    forged = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
        key=WRONG_KEY,
    )
    email = sign_receipt(packet, policy, 1, outcome="SUCCESS_CONFIRMED")
    req = {"packet": packet, "policy": policy, "attempts": [forged, email]}
    with pytest.raises(SubmissionTransportInputError, match="unverified provider authority"):
        compile_transport_decision(req, authority_verifier=verifier())


def test_authenticated_unallowlisted_terminal_failure_holds():
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_TERMINAL_POLICY_REJECTED",
    )
    req = {"packet": packet, "policy": policy, "attempts": [failed]}
    receipt = compile_transport_decision(req, authority_verifier=verifier())
    check(receipt["disposition"] == "HOLD_FAILURE_NOT_AUTHORIZED_FOR_FAILOVER")
    check(receipt["next_route"] is None)


def test_ambiguous_like_failure_class_must_use_ambiguous_outcome():
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_TIMEOUT_CONFIRMED",
    )
    req = {"packet": packet, "policy": policy, "attempts": [failed]}
    with pytest.raises(SubmissionTransportInputError, match="ambiguous/non-terminal"):
        compile_transport_decision(req, authority_verifier=verifier())


def test_advancing_after_authenticated_unallowlisted_failure_is_rejected():
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_TERMINAL_POLICY_REJECTED",
    )
    email = sign_receipt(packet, policy, 1, outcome="SUCCESS_CONFIRMED")
    req = {"packet": packet, "policy": policy, "attempts": [failed, email]}
    with pytest.raises(SubmissionTransportInputError, match="unapproved failure"):
        compile_transport_decision(req, authority_verifier=verifier())


def test_exhausted_policy_holds_after_authenticated_allowlisted_failure():
    packet = ready_packet()
    policy = route_policy(packet, include_email=False)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    req = {"packet": packet, "policy": policy, "attempts": [failed]}
    receipt = compile_transport_decision(req, authority_verifier=verifier({GH_AUTHORITY: GH_KEY}))
    check(receipt["disposition"] == "HOLD_NO_AUTHORIZED_ROUTE_REMAINS")


def test_route_skipping_or_receipt_replay_is_rejected():
    packet = ready_packet()
    policy = route_policy(packet)
    email = sign_receipt(packet, policy, 1, outcome="SUCCESS_CONFIRMED")
    req = {"packet": packet, "policy": policy, "attempts": [email]}
    with pytest.raises(SubmissionTransportInputError, match="authority|route"):
        compile_transport_decision(req, authority_verifier=verifier())


@pytest.mark.parametrize(
    "field,bad,pattern",
    [
        ("packet_sha256", "9" * 64, "packet binding"),
        ("head_sha", "9" * 40, "head binding"),
        ("artifact_evidence_sha256", "9" * 64, "artifact evidence binding"),
        ("policy_sha256", "9" * 64, "policy binding"),
        ("operation_sha256", "9" * 64, "operation binding"),
    ],
)
def test_exact_operation_artifact_policy_bindings_block_replay(field, bad, pattern):
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    failed[field] = bad
    failed["auth_tag_hmac_sha256"] = hmac.new(
        GH_KEY,
        canonical({k: v for k, v in failed.items() if k != "auth_tag_hmac_sha256"}),
        hashlib.sha256,
    ).hexdigest()
    req = {"packet": packet, "policy": policy, "attempts": [failed]}
    with pytest.raises(SubmissionTransportInputError, match=pattern):
        compile_transport_decision(req, authority_verifier=verifier())


def test_route_authority_id_is_bound_by_policy():
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    failed["authority_id"] = EMAIL_AUTHORITY
    req = {"packet": packet, "policy": policy, "attempts": [failed]}
    with pytest.raises(SubmissionTransportInputError, match="authority does not match"):
        compile_transport_decision(req, authority_verifier=verifier())


def test_duplicate_attempt_ids_are_rejected():
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
        attempt_id="same-attempt",
    )
    email = sign_receipt(
        packet,
        policy,
        1,
        outcome="SUCCESS_CONFIRMED",
        attempt_id="same-attempt",
    )
    req = {"packet": packet, "policy": policy, "attempts": [failed, email]}
    with pytest.raises(SubmissionTransportInputError, match="attempt IDs must be unique"):
        compile_transport_decision(req, authority_verifier=verifier())


def test_duplicate_route_ids_are_rejected():
    packet = ready_packet()
    policy = route_policy(packet)
    policy["routes"][1]["route_id"] = policy["routes"][0]["route_id"]
    policy["policy_sha256"] = digest({k: v for k, v in policy.items() if k != "policy_sha256"})
    with pytest.raises(SubmissionTransportInputError, match="route IDs must be unique"):
        compile_transport_decision({"packet": packet, "policy": policy, "attempts": []})


def test_operation_descriptor_is_deterministic_and_route_specific():
    packet = ready_packet()
    policy = route_policy(packet)
    first = compile_transport_operation(packet, policy, 0)
    first_again = compile_transport_operation(packet, policy, 0)
    second = compile_transport_operation(packet, policy, 1)
    check(first == first_again)
    check(first["operation_sha256"] != second["operation_sha256"])
    check(first["authority_id"] == GH_AUTHORITY)
    check(second["authority_id"] == EMAIL_AUTHORITY)


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


def test_authority_verifier_exception_fails_closed():
    packet = ready_packet()
    policy = route_policy(packet)
    successful = sign_receipt(packet, policy, 0, outcome="SUCCESS_CONFIRMED")

    def exploding(_receipt):
        raise RuntimeError("adapter unavailable")

    with pytest.raises(SubmissionTransportInputError, match="failed closed"):
        compile_transport_decision(
            {"packet": packet, "policy": policy, "attempts": [successful]},
            authority_verifier=exploding,
        )


def test_authority_verifier_must_return_bool():
    packet = ready_packet()
    policy = route_policy(packet)
    successful = sign_receipt(packet, policy, 0, outcome="SUCCESS_CONFIRMED")
    with pytest.raises(SubmissionTransportInputError, match="must return bool"):
        compile_transport_decision(
            {"packet": packet, "policy": policy, "attempts": [successful]},
            authority_verifier=lambda _receipt: 1,
        )


def test_hmac_keyring_rejects_short_or_text_keys():
    with pytest.raises(SubmissionTransportInputError, match="32..128"):
        make_hmac_authority_verifier({GH_AUTHORITY: b"short"})
    with pytest.raises(SubmissionTransportInputError, match="32..128"):
        make_hmac_authority_verifier({GH_AUTHORITY: "11" * 32})


def test_strict_json_rejects_duplicate_keys_and_nonfinite_constants():
    with pytest.raises(SubmissionTransportInputError, match="duplicate JSON key"):
        strict_json_loads('{"packet":{},"packet":{}}')
    with pytest.raises(SubmissionTransportInputError, match="non-finite"):
        strict_json_loads('{"x":NaN}')


def test_receipt_verifier_rejects_mutation_and_needs_same_authority_boundary():
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    req = {"packet": packet, "policy": policy, "attempts": [failed]}
    authority = verifier()
    receipt = compile_transport_decision(req, authority_verifier=authority)
    check(verify_transport_decision(req, receipt, authority_verifier=authority))
    check(not verify_transport_decision(req, receipt))
    tampered = copy.deepcopy(receipt)
    tampered["next_route"]["route_id"] = "attacker-route"
    check(not verify_transport_decision(req, tampered, authority_verifier=authority))


def test_public_receipt_never_exposes_hmac_tag_or_send_authority():
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    req = {"packet": packet, "policy": policy, "attempts": [failed]}
    receipt = compile_transport_decision(req, authority_verifier=verifier())
    rendered = json.dumps(receipt, sort_keys=True)
    check("auth_tag_hmac_sha256" not in rendered)
    check(GH_KEY.hex() not in rendered)
    check("@example" not in rendered)
    check("mailto:" not in rendered)
    check(receipt["authority"]["external_send_authorized"] is False)
    check(receipt["authority"]["global_outbound_lease_required"] is True)
    check(receipt["authority"]["sponsor_route_authenticity_inferred"] is False)
    check(receipt["authority"]["sponsor_acceptance_inferred"] is False)
    check(receipt["authority"]["payment_inferred"] is False)
    check(receipt["authority"]["cash_claim"] is False)
    core = dict(receipt)
    supplied = core.pop("decision_sha256")
    check(supplied == digest(core))
