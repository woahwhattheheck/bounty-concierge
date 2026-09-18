import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from concierge import _submission_transport_router_core as core
from concierge import submission_transport_router as public


SOURCE = "https://github.com/example/project/issues/315"
HEX40 = "a" * 40
EVIDENCE = "b" * 64
GH_AUTHORITY = "github-app-adapter-v1"
EMAIL_AUTHORITY = "smtp-adapter-v1"


def check(condition, message="check failed"):
    if not condition:
        raise AssertionError(message)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


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
            "acceptance_checks": [
                {"criterion_id": "tests", "status": "PASS"}
            ],
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


def rehash_packet(packet):
    packet["packet_sha256"] = digest(
        {key: value for key, value in packet.items() if key != "packet_sha256"}
    )
    return packet


def route_policy(packet, include_email=True):
    routes = [
        {
            "route_id": "upstream-github",
            "route_class": "github-pr",
            "authority_id": GH_AUTHORITY,
            "route_evidence_sha256": "d" * 64,
            "failover_on": ["PROVIDER_403_INTEGRATION_FORBIDDEN"],
        }
    ]
    if include_email:
        routes.append(
            {
                "route_id": "sponsor-email",
                "route_class": "email",
                "authority_id": EMAIL_AUTHORITY,
                "route_evidence_sha256": "e" * 64,
                "failover_on": ["PROVIDER_TERMINAL_REJECTED"],
            }
        )
    policy = {
        "schema": "sponsor-submission-transport-policy/v1",
        "policy_id": "host-root-v2",
        "canonical_source_url": SOURCE,
        "packet_sha256": packet["packet_sha256"],
        "policy_evidence_sha256": "c" * 64,
        "routes": routes,
    }
    policy["policy_sha256"] = digest(policy)
    return policy


def provider_receipt(packet, policy, index, *, outcome, failure=None):
    operation = core.compile_transport_operation(packet, policy, index)
    route = policy["routes"][index]
    return {
        "schema": "provider-attempt-authority/v1",
        "authority_id": route["authority_id"],
        "operation_sha256": operation["operation_sha256"],
        "attempt_id": f"try-{index + 1}",
        "route_id": route["route_id"],
        "packet_sha256": packet["packet_sha256"],
        "head_sha": HEX40,
        "artifact_evidence_sha256": EVIDENCE,
        "policy_sha256": policy["policy_sha256"],
        "outcome": outcome,
        "failure_class": failure,
        "provider_evidence_sha256": ("f" if index == 0 else "1") * 64,
        "auth_tag_hmac_sha256": ("2" if index == 0 else "3") * 64,
    }


def request_with_attempt(*, outcome="FAILED_CONFIRMED", failure="PROVIDER_403_INTEGRATION_FORBIDDEN"):
    packet = ready_packet()
    policy = route_policy(packet)
    attempt = provider_receipt(
        packet, policy, 0, outcome=outcome, failure=failure
    )
    return {"packet": packet, "policy": policy, "attempts": [attempt]}


def test_public_primary_route_needs_no_provider_outcome_authority():
    packet = ready_packet()
    req = {"packet": packet, "policy": route_policy(packet), "attempts": []}
    receipt = public.compile_transport_decision(req)
    check(receipt["schema"] == "submission-transport-decision/v2")
    check(receipt["disposition"] == "READY_PRIMARY")
    check(receipt["next_route"]["route_id"] == "upstream-github")
    check(receipt["authority"]["direct_core_production_safe"] is True)
    check(receipt["authority"]["canonical_packet_verifier"] == "submission_custody._verify_submission_packet")
    check(receipt["authority"]["caller_verifier_injection_supported"] is False)
    check(receipt["authority"]["caller_keyring_selection_supported"] is False)
    check(receipt["authority"]["caller_trust_root_selection_supported"] is False)


def test_public_and_core_compile_are_the_same_production_function():
    # Peer boundary suites legitimately ``importlib.reload`` the core in place,
    # which rebinds core's functions while the public module keeps the bindings
    # captured at its own import. Resync the re-export before the identity check.
    importlib.reload(public)
    check(public.compile_transport_decision is core.compile_transport_decision)
    check(public.compile_transport_operation is core.compile_transport_operation)
    check(public.verify_transport_decision is core.verify_transport_decision)


def test_public_compile_rejects_lambda_verifier_injection_at_signature():
    packet = ready_packet()
    req = {"packet": packet, "policy": route_policy(packet), "attempts": []}
    with pytest.raises(TypeError):
        public.compile_transport_decision(req, authority_verifier=lambda _: True)


def test_direct_core_compile_rejects_lambda_verifier_injection_at_signature():
    packet = ready_packet()
    req = {"packet": packet, "policy": route_policy(packet), "attempts": []}
    with pytest.raises(TypeError):
        core.compile_transport_decision(req, authority_verifier=lambda _: True)


def test_public_and_core_verify_reject_verifier_injection():
    packet = ready_packet()
    req = {"packet": packet, "policy": route_policy(packet), "attempts": []}
    receipt = public.compile_transport_decision(req)
    with pytest.raises(TypeError):
        public.verify_transport_decision(req, receipt, authority_verifier=lambda _: True)
    with pytest.raises(TypeError):
        core.verify_transport_decision(req, receipt, authority_verifier=lambda _: True)


def test_supported_modules_do_not_export_verifier_or_keyring_builder():
    check("AuthorityVerifier" not in public.__all__)
    check("make_hmac_authority_verifier" not in public.__all__)
    check(not hasattr(public, "AuthorityVerifier"))
    check(not hasattr(public, "make_hmac_authority_verifier"))
    check(not hasattr(core, "make_hmac_authority_verifier"))


def test_production_compile_signature_has_only_request():
    check(list(inspect.signature(core.compile_transport_decision).parameters) == ["request"])
    check(list(inspect.signature(public.compile_transport_decision).parameters) == ["request"])


def test_public_and_direct_core_cli_have_no_authority_selector(tmp_path):
    request_path = tmp_path / "request.json"
    request_path.write_text("{}", encoding="utf-8")
    for main in (public.main, core.main):
        with pytest.raises(SystemExit):
            main([str(request_path), "--authority-keyring", str(tmp_path / "attacker.json")])
        with pytest.raises(SystemExit):
            main([str(request_path), "--authority-ledger", str(tmp_path / "attacker")])


def test_claimed_failure_without_fixed_host_receipt_holds():
    fixed_ledger = Path("/var/lib/bounty-concierge/provider-attempt-authority")
    if fixed_ledger.exists():
        pytest.skip("host receipt ledger is provisioned in this environment")
    receipt = core.compile_transport_decision(request_with_attempt())
    check(receipt["disposition"] == "HOLD_PROVIDER_AUTHORITY_UNVERIFIED")
    check(receipt["next_route"] is None)
    check(receipt["completed_attempts"][0]["authority_verified"] is False)
    check("outcome" not in receipt["completed_attempts"][0])
    check("failure_class" not in receipt["completed_attempts"][0])
    check("provider_evidence_sha256" not in receipt["completed_attempts"][0])


def test_environment_cannot_select_receipt_ledger(monkeypatch):
    fixed_ledger = Path("/var/lib/bounty-concierge/provider-attempt-authority")
    if fixed_ledger.exists():
        pytest.skip("host receipt ledger is provisioned in this environment")
    monkeypatch.setenv("BOUNTY_CONCIERGE_PROVIDER_RECEIPT_LEDGER", "/tmp/attacker")
    monkeypatch.setenv("AUTHORITY_KEYRING", "/tmp/attacker.json")
    receipt = core.compile_transport_decision(request_with_attempt())
    check(receipt["disposition"] == "HOLD_PROVIDER_AUTHORITY_UNVERIFIED")
    source = inspect.getsource(core._host_receipt_authority_verifier)
    check('Path("/var/lib/bounty-concierge/provider-attempt-authority")' in source)


def test_test_only_lambda_true_observation_cannot_be_production_authority():
    observation = core._compile_transport_test_observation(
        request_with_attempt(), authority_verifier=lambda _: True
    )
    check(observation["schema"] == "submission-transport-test-observation/v1")
    check(observation["production_disposition"] == "HOLD_TEST_ONLY_AUTHORITY")
    check(observation["candidate_disposition"] == "READY_FALLBACK")
    check(observation["candidate_next_route_id"] == "sponsor-email")
    check(observation["next_route"] is None)
    check(observation["production_authority_valid"] is False)
    check(observation["test_only_dependency_injection"] is True)
    check("decision_sha256" not in observation)


def test_test_only_success_observation_cannot_be_already_submitted_production_receipt():
    observation = core._compile_transport_test_observation(
        request_with_attempt(outcome="SUCCESS_CONFIRMED", failure=None),
        authority_verifier=lambda _: True,
    )
    check(observation["candidate_disposition"] == "ALREADY_SUBMITTED")
    check(observation["production_disposition"] == "HOLD_TEST_ONLY_AUTHORITY")
    check(observation["next_route"] is None)


def test_test_only_ambiguous_observation_holds():
    observation = core._compile_transport_test_observation(
        request_with_attempt(outcome="AMBIGUOUS", failure=None),
        authority_verifier=lambda _: True,
    )
    check(observation["candidate_disposition"] == "HOLD_AMBIGUOUS_PROVIDER_OUTCOME")
    check(observation["candidate_next_route_id"] is None)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("packet_sha256", "9" * 64),
        ("head_sha", "9" * 40),
        ("artifact_evidence_sha256", "9" * 64),
        ("policy_sha256", "9" * 64),
        ("operation_sha256", "9" * 64),
    ],
)
def test_replayed_receipt_bindings_are_rejected_before_authority(field, bad):
    req = request_with_attempt()
    req["attempts"][0][field] = bad
    with pytest.raises(core.SubmissionTransportInputError):
        core._compile_transport_test_observation(
            req, authority_verifier=lambda _: True
        )


def test_direct_core_enforces_canonical_changed_path_allowlist():
    packet = ready_packet()
    packet["evidence"]["changed_paths"] = ["src/forbidden.py"]
    rehash_packet(packet)
    with pytest.raises(core.SubmissionTransportInputError, match="PACKET_CHANGED_PATH_OUTSIDE_ALLOWLIST"):
        core.compile_transport_decision(
            {"packet": packet, "policy": route_policy(packet), "attempts": []}
        )


def test_direct_core_enforces_canonical_positive_reward():
    packet = ready_packet()
    packet["advertised_reward_usd"] = "0"
    rehash_packet(packet)
    with pytest.raises(core.SubmissionTransportInputError, match="PACKET_REWARD_INVALID"):
        core.compile_transport_decision(
            {"packet": packet, "policy": route_policy(packet), "attempts": []}
        )


def test_direct_core_rejects_stripped_packet_evidence():
    packet = ready_packet()
    del packet["evidence"]["tests"]
    rehash_packet(packet)
    with pytest.raises(core.SubmissionTransportInputError, match="PACKET_EVIDENCE_SHAPE_MISMATCH"):
        core.compile_transport_decision(
            {"packet": packet, "policy": route_policy(packet), "attempts": []}
        )


def test_direct_core_rejects_nonpassing_packet_tests():
    packet = ready_packet()
    packet["evidence"]["tests"][0]["outcome"] = "FAIL"
    rehash_packet(packet)
    with pytest.raises(core.SubmissionTransportInputError, match="PACKET_TEST_NOT_PASS"):
        core.compile_transport_decision(
            {"packet": packet, "policy": route_policy(packet), "attempts": []}
        )


def test_direct_core_rejects_nonpassing_acceptance():
    packet = ready_packet()
    packet["evidence"]["acceptance_checks"][0]["status"] = "FAIL"
    rehash_packet(packet)
    with pytest.raises(core.SubmissionTransportInputError, match="PACKET_ACCEPTANCE_NOT_PASS"):
        core.compile_transport_decision(
            {"packet": packet, "policy": route_policy(packet), "attempts": []}
        )


def test_operation_compiler_also_uses_canonical_packet_verifier():
    packet = ready_packet()
    packet["advertised_reward_usd"] = "0"
    rehash_packet(packet)
    with pytest.raises(core.SubmissionTransportInputError, match="PACKET_REWARD_INVALID"):
        core.compile_transport_operation(packet, route_policy(packet), 0)


def test_public_decision_verifier_recompiles_fixed_trust_path():
    packet = ready_packet()
    req = {"packet": packet, "policy": route_policy(packet), "attempts": []}
    receipt = public.compile_transport_decision(req)
    check(public.verify_transport_decision(req, receipt))
    tampered = dict(receipt)
    tampered["disposition"] = "READY_FALLBACK"
    check(not public.verify_transport_decision(req, tampered))


def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers():
    with pytest.raises(core.SubmissionTransportInputError, match="duplicate JSON key"):
        core.strict_json_loads('{"x":1,"x":2}')
    with pytest.raises(core.SubmissionTransportInputError, match="non-finite"):
        core.strict_json_loads('{"x":NaN}')


def test_insecure_non_root_receipt_directory_is_rejected(tmp_path):
    operation = "a" * 64
    (tmp_path / f"{operation}.json").write_text("{}", encoding="utf-8")
    # On non-root CI the ownership check rejects it. When tests run as root,
    # making the directory group-writable exercises the same fail-closed path.
    tmp_path.chmod(0o770)
    check(core._read_host_receipt_from_directory(tmp_path, operation) is None)


def test_format_summary_never_implies_send_authority():
    packet = ready_packet()
    receipt = core.compile_transport_decision(
        {"packet": packet, "policy": route_policy(packet), "attempts": []}
    )
    summary = core.format_summary(receipt)
    check("external_send_authorized=false" in summary)
    check("global_outbound_lease_required=true" in summary)


def _subprocess_env(project_root: Path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(project_root)
    return env


def test_fresh_process_direct_core_cli_rejects_attacker_keyring(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    req = request_with_attempt()
    request_path = tmp_path / "request.json"
    keyring_path = tmp_path / "attacker.json"
    request_path.write_text(json.dumps(req), encoding="utf-8")
    keyring_path.write_text(json.dumps({GH_AUTHORITY: "11" * 32}), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "concierge._submission_transport_router_core",
            str(request_path),
            "--authority-keyring",
            str(keyring_path),
        ],
        cwd=project_root,
        env=_subprocess_env(project_root),
        text=True,
        capture_output=True,
        check=False,
    )
    check(completed.returncode == 2)
    combined = completed.stdout + completed.stderr
    check("unrecognized arguments: --authority-keyring" in combined)
    check("READY_FALLBACK" not in combined)
    check("ALREADY_SUBMITTED" not in combined)


def test_fresh_process_direct_core_enforces_positive_reward(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    packet = ready_packet()
    packet["advertised_reward_usd"] = "0"
    rehash_packet(packet)
    req = {"packet": packet, "policy": route_policy(packet), "attempts": []}
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(req), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "concierge._submission_transport_router_core", str(request_path)],
        cwd=project_root,
        env=_subprocess_env(project_root),
        text=True,
        capture_output=True,
        check=False,
    )
    check(completed.returncode == 2)
    combined = completed.stdout + completed.stderr
    check("PACKET_REWARD_INVALID" in combined)
    check("READY_PRIMARY" not in combined)


def test_fresh_process_direct_core_enforces_evidence_shape(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    packet = ready_packet()
    del packet["evidence"]["acceptance_checks"]
    rehash_packet(packet)
    req = {"packet": packet, "policy": route_policy(packet), "attempts": []}
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(req), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "concierge._submission_transport_router_core", str(request_path)],
        cwd=project_root,
        env=_subprocess_env(project_root),
        text=True,
        capture_output=True,
        check=False,
    )
    check(completed.returncode == 2)
    combined = completed.stdout + completed.stderr
    check("PACKET_EVIDENCE_SHAPE_MISMATCH" in combined)
    check("READY_PRIMARY" not in combined)
