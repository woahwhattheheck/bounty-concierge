import hashlib
import json

import pytest

from concierge.submission_custody import (
    SubmissionCustodyError,
    empty_ledger,
    evaluate_submission,
    record_dispatch,
    record_follow_up,
    record_sponsor_event,
    register_candidate,
    strict_json_loads,
    supersede_candidate,
    verify_ledger,
)

AS_OF = "2026-09-13T12:00:00Z"


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def packet(*, issue=315, evidence_sha=None, head=None, reward="45"):
    evidence_sha = evidence_sha or sha("artifact-v1")
    head = head or "a" * 40
    core = {
        "canonical_source_url": f"https://github.com/Scottcjn/rustchain-bounties/issues/{issue}",
        "advertised_reward_usd": reward,
        "disposition": "READY_FOR_HUMAN_SUBMISSION",
        "reason_codes": [],
        "evidence": {
            "pull_request_url": "https://github.com/Scottcjn/rustchain-bounties/pull/999",
            "pull_request_repo": "Scottcjn/rustchain-bounties",
            "pull_request_number": 999,
            "head_sha": head,
            "changed_paths": ["deliverables/asset.md"],
            "allowed_paths": ["deliverables/asset.md"],
            "tests": [{"command": "pytest -q", "outcome": "PASS"}],
            "evidence_sha256": evidence_sha,
            "acceptance_checks": [{"criterion_id": "stage1", "status": "PASS"}],
        },
        "authority": {
            "submission": "human_only",
            "reward": "advertised_only",
            "acceptance": "not_inferred",
            "payout": "not_inferred",
            "cash_claim": False,
        },
    }
    canonical = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {**core, "packet_sha256": hashlib.sha256(canonical).hexdigest()}


def expect_code(code, fn):
    with pytest.raises(SubmissionCustodyError) as exc:
        fn()
    assert exc.value.code == code


def registered(*, artifact_revision=1, packet_value=None, sid="SUB-315-v1", event_id="EV-REG-1", occurred="2026-09-13T10:00:00Z"):
    return register_candidate(
        empty_ledger(),
        event_id=event_id,
        occurred_at=occurred,
        submission_id=sid,
        packet=packet_value or packet(),
        artifact_revision=artifact_revision,
        route_class="email_fallback",
        route_key="rustchain-sponsor-fallback",
        contract_digest="",
        as_of=AS_OF,
    )


def dispatch(reg, *, event_id="EV-SEND-1", occurred="2026-09-13T10:05:00Z", receipt="gmail-msg-001"):
    r = reg["receipt"] if "receipt" in reg else evaluate_submission(reg["ledger"], "SUB-315-v1", as_of=AS_OF, follow_up_after_seconds=86400)
    return record_dispatch(
        reg["ledger"],
        event_id=event_id,
        occurred_at=occurred,
        submission_id="SUB-315-v1",
        submission_digest=r["submission_digest"],
        route_class="email_fallback",
        provider_receipt_key=receipt,
        evidence_digest=sha("sent-proof" + receipt),
        as_of=AS_OF,
    )


def sponsor(sent, status, *, event_id="EV-SP-1", occurred="2026-09-13T10:10:00Z", key="sponsor-event-001"):
    r = evaluate_submission(sent["ledger"], "SUB-315-v1", as_of=AS_OF, follow_up_after_seconds=86400)
    return record_sponsor_event(
        sent["ledger"],
        event_id=event_id,
        occurred_at=occurred,
        submission_id="SUB-315-v1",
        submission_digest=r["submission_digest"],
        status=status,
        provider_event_key=key,
        evidence_digest=sha(status + key),
        as_of=AS_OF,
    )


def test_ready_packet_registers_deterministically_and_is_sendable():
    result = registered()
    assert result["replayed"] is False
    receipt = result["receipt"]
    assert receipt["send_disposition"] == "READY_TO_SUBMIT"
    assert receipt["lifecycle_state"] == "READY_TO_SUBMIT"
    assert receipt["artifact_digest"] == sha("artifact-v1")
    assert all(value is False for value in receipt["authority"].values())
    assert verify_ledger(result["ledger"], as_of=AS_OF)["valid"] is True


def test_register_exact_event_replay_is_idempotent():
    first = registered()
    second = register_candidate(
        first["ledger"], event_id="EV-REG-1", occurred_at="2026-09-13T10:00:00Z",
        submission_id="SUB-315-v1", packet=packet(), artifact_revision=1,
        route_class="email_fallback", route_key="rustchain-sponsor-fallback", contract_digest="", as_of=AS_OF,
    )
    assert second["replayed"] is True
    assert second["ledger"] == first["ledger"]


def test_changed_bytes_under_same_event_id_conflict():
    first = registered()
    expect_code("EVENT_ID_CONFLICT", lambda: register_candidate(
        first["ledger"], event_id="EV-REG-1", occurred_at="2026-09-13T10:00:00Z",
        submission_id="SUB-315-v2", packet=packet(evidence_sha=sha("other")), artifact_revision=2,
        route_class="email_fallback", route_key="rustchain-sponsor-fallback", contract_digest="", as_of=AS_OF,
    ))


def test_packet_digest_tamper_fails_closed():
    p = packet()
    p["advertised_reward_usd"] = "999"
    expect_code("PACKET_DIGEST_MISMATCH", lambda: registered(packet_value=p))


def test_packet_must_be_ready_and_human_only():
    p = packet()
    p["disposition"] = "HOLD"
    core = {k: v for k, v in p.items() if k != "packet_sha256"}
    p["packet_sha256"] = hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    expect_code("PACKET_NOT_READY", lambda: registered(packet_value=p))

    p = packet()
    p["authority"]["cash_claim"] = True
    core = {k: v for k, v in p.items() if k != "packet_sha256"}
    p["packet_sha256"] = hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    expect_code("PACKET_AUTHORITY_MISMATCH", lambda: registered(packet_value=p))


def test_source_revision_conflict_prevents_parallel_duplicate_candidate():
    first = registered()
    expect_code("SOURCE_REVISION_CONFLICT", lambda: register_candidate(
        first["ledger"], event_id="EV-REG-2", occurred_at="2026-09-13T10:01:00Z",
        submission_id="SUB-315-dup", packet=packet(), artifact_revision=1,
        route_class="github_comment", route_key="rustchain-issue-comment", contract_digest="", as_of=AS_OF,
    ))


def test_dispatch_converts_send_state_to_already_submitted():
    reg = registered()
    sent = dispatch(reg)
    r = evaluate_submission(sent["ledger"], "SUB-315-v1", as_of="2026-09-13T10:06:00Z", follow_up_after_seconds=3600)
    assert r["send_disposition"] == "ALREADY_SUBMITTED"
    assert r["lifecycle_state"] == "AWAITING_SPONSOR"
    assert r["outbound_event_count"] == 1


def test_second_primary_dispatch_is_rejected_even_with_new_receipt():
    reg = registered()
    sent = dispatch(reg)
    r = evaluate_submission(sent["ledger"], "SUB-315-v1", as_of=AS_OF, follow_up_after_seconds=86400)
    expect_code("MULTIPLE_PRIMARY_DISPATCH", lambda: record_dispatch(
        sent["ledger"], event_id="EV-SEND-2", occurred_at="2026-09-13T10:06:00Z",
        submission_id="SUB-315-v1", submission_digest=r["submission_digest"], route_class="email_fallback",
        provider_receipt_key="gmail-msg-002", evidence_digest=sha("second-send"), as_of=AS_OF,
    ))


def test_provider_receipt_cannot_be_reused_across_candidates():
    first = dispatch(registered())
    second_packet = packet(issue=316, evidence_sha=sha("artifact-316"), head="b" * 40)
    reg2 = register_candidate(
        first["ledger"], event_id="EV-REG-2", occurred_at="2026-09-13T10:06:00Z",
        submission_id="SUB-316-v1", packet=second_packet, artifact_revision=1, route_class="email_fallback",
        route_key="rustchain-sponsor-fallback", contract_digest="", as_of=AS_OF,
    )
    r2 = evaluate_submission(reg2["ledger"], "SUB-316-v1", as_of=AS_OF, follow_up_after_seconds=86400)
    expect_code("EXTERNAL_REFERENCE_REUSE", lambda: record_dispatch(
        reg2["ledger"], event_id="EV-SEND-2", occurred_at="2026-09-13T10:07:00Z", submission_id="SUB-316-v1",
        submission_digest=r2["submission_digest"], route_class="email_fallback", provider_receipt_key="gmail-msg-001",
        evidence_digest=sha("send316"), as_of=AS_OF,
    ))


def test_route_mismatch_is_rejected():
    reg = registered()
    r = reg["receipt"]
    expect_code("ROUTE_CLASS_MISMATCH", lambda: record_dispatch(
        reg["ledger"], event_id="EV-SEND-1", occurred_at="2026-09-13T10:05:00Z",
        submission_id="SUB-315-v1", submission_digest=r["submission_digest"], route_class="github_comment",
        provider_receipt_key="comment-001", evidence_digest=sha("x"), as_of=AS_OF,
    ))


def test_future_dispatch_and_timestamp_inversion_fail_closed():
    reg = registered()
    r = reg["receipt"]
    expect_code("FUTURE_EVENT", lambda: record_dispatch(
        reg["ledger"], event_id="EV-SEND-1", occurred_at="2026-09-13T12:01:00Z",
        submission_id="SUB-315-v1", submission_digest=r["submission_digest"], route_class="email_fallback",
        provider_receipt_key="gmail-msg-001", evidence_digest=sha("x"), as_of=AS_OF,
    ))
    expect_code("TIMESTAMP_INVERSION", lambda: record_dispatch(
        reg["ledger"], event_id="EV-SEND-1", occurred_at="2026-09-13T09:59:59Z",
        submission_id="SUB-315-v1", submission_digest=r["submission_digest"], route_class="email_fallback",
        provider_receipt_key="gmail-msg-001", evidence_digest=sha("x"), as_of=AS_OF,
    ))


def test_naive_or_noncanonical_timestamps_are_rejected():
    expect_code("INVALID_TIMESTAMP", lambda: registered(occurred="2026-09-13T10:00:00"))
    expect_code("NONCANONICAL_TIMESTAMP", lambda: registered(occurred="2026-09-13T10:00:00.000000Z"))


def test_follow_up_due_uses_trusted_as_of_and_policy_not_inference():
    sent = dispatch(registered())
    early = evaluate_submission(sent["ledger"], "SUB-315-v1", as_of="2026-09-13T11:04:59Z", follow_up_after_seconds=3600)
    assert early["lifecycle_state"] == "AWAITING_SPONSOR"
    due = evaluate_submission(sent["ledger"], "SUB-315-v1", as_of="2026-09-13T11:05:00Z", follow_up_after_seconds=3600)
    assert due["lifecycle_state"] == "FOLLOW_UP_DUE"


def test_recorded_follow_up_resets_follow_up_clock_but_never_resends_primary():
    sent = dispatch(registered())
    r = evaluate_submission(sent["ledger"], "SUB-315-v1", as_of=AS_OF, follow_up_after_seconds=3600)
    follow = record_follow_up(
        sent["ledger"], event_id="EV-FU-1", occurred_at="2026-09-13T11:00:00Z", submission_id="SUB-315-v1",
        submission_digest=r["submission_digest"], route_class="email_fallback", provider_receipt_key="gmail-followup-001",
        evidence_digest=sha("followup"), as_of=AS_OF,
    )
    mid = evaluate_submission(follow["ledger"], "SUB-315-v1", as_of="2026-09-13T11:59:59Z", follow_up_after_seconds=3600)
    assert mid["send_disposition"] == "ALREADY_SUBMITTED"
    assert mid["lifecycle_state"] == "AWAITING_SPONSOR"
    due = evaluate_submission(follow["ledger"], "SUB-315-v1", as_of="2026-09-13T12:00:00Z", follow_up_after_seconds=3600)
    assert due["lifecycle_state"] == "FOLLOW_UP_DUE"


def test_sponsor_ack_does_not_infer_acceptance_or_payment():
    ack = sponsor(dispatch(registered()), "acknowledged")
    r = evaluate_submission(ack["ledger"], "SUB-315-v1", as_of="2026-09-13T10:20:00Z", follow_up_after_seconds=3600)
    assert r["lifecycle_state"] == "AWAITING_SPONSOR"
    assert r["authority"]["acceptance_inferred"] is False
    assert r["authority"]["payment_inferred"] is False


def test_needs_changes_is_explicit_state():
    changed = sponsor(dispatch(registered()), "needs_changes")
    r = evaluate_submission(changed["ledger"], "SUB-315-v1", as_of=AS_OF, follow_up_after_seconds=3600)
    assert r["lifecycle_state"] == "NEEDS_CHANGES"
    assert r["send_disposition"] == "ALREADY_SUBMITTED"


def test_acceptance_to_payment_pending_to_settlement_evidence_ready():
    accepted = sponsor(dispatch(registered()), "accepted", event_id="EV-SP-A", key="sponsor-accept-001")
    r = evaluate_submission(accepted["ledger"], "SUB-315-v1", as_of=AS_OF, follow_up_after_seconds=3600)
    assert r["lifecycle_state"] == "ACCEPTED_AWAITING_SETTLEMENT"
    pending = record_sponsor_event(
        accepted["ledger"], event_id="EV-SP-P", occurred_at="2026-09-13T10:11:00Z", submission_id="SUB-315-v1",
        submission_digest=r["submission_digest"], status="payment_pending", provider_event_key="sponsor-paypending-001",
        evidence_digest=sha("pending"), as_of=AS_OF,
    )
    r2 = evaluate_submission(pending["ledger"], "SUB-315-v1", as_of=AS_OF, follow_up_after_seconds=3600)
    ready = record_sponsor_event(
        pending["ledger"], event_id="EV-SP-R", occurred_at="2026-09-13T10:12:00Z", submission_id="SUB-315-v1",
        submission_digest=r2["submission_digest"], status="paid_evidence_ready", provider_event_key="sponsor-paid-proof-001",
        evidence_digest=sha("paid-proof"), as_of=AS_OF,
    )
    r3 = evaluate_submission(ready["ledger"], "SUB-315-v1", as_of=AS_OF, follow_up_after_seconds=3600)
    assert r3["lifecycle_state"] == "SETTLEMENT_EVIDENCE_READY"
    assert r3["authority"]["payment_inferred"] is False
    assert r3["authority"]["revenue_recognized"] is False


def test_payment_state_without_acceptance_is_rejected():
    sent = dispatch(registered())
    r = evaluate_submission(sent["ledger"], "SUB-315-v1", as_of=AS_OF, follow_up_after_seconds=3600)
    expect_code("PAYMENT_PENDING_WITHOUT_ACCEPTANCE", lambda: record_sponsor_event(
        sent["ledger"], event_id="EV-SP-P", occurred_at="2026-09-13T10:10:00Z", submission_id="SUB-315-v1",
        submission_digest=r["submission_digest"], status="payment_pending", provider_event_key="sponsor-pending-001",
        evidence_digest=sha("pending"), as_of=AS_OF,
    ))


def test_rejected_and_withdrawn_are_terminal_holds():
    rejected = sponsor(dispatch(registered()), "rejected")
    r = evaluate_submission(rejected["ledger"], "SUB-315-v1", as_of=AS_OF, follow_up_after_seconds=3600)
    assert (r["send_disposition"], r["lifecycle_state"]) == ("HOLD", "CLOSED_REJECTED")
    expect_code("TERMINAL_RESPONSE_REOPEN", lambda: record_sponsor_event(
        rejected["ledger"], event_id="EV-SP-2", occurred_at="2026-09-13T10:11:00Z", submission_id="SUB-315-v1",
        submission_digest=r["submission_digest"], status="acknowledged", provider_event_key="sponsor-event-002",
        evidence_digest=sha("ack"), as_of=AS_OF,
    ))


def test_acceptance_cannot_regress_to_needs_changes_without_explicit_new_revision():
    accepted = sponsor(dispatch(registered()), "accepted", key="accepted-001")
    r = evaluate_submission(accepted["ledger"], "SUB-315-v1", as_of=AS_OF, follow_up_after_seconds=3600)
    expect_code("ACCEPTED_RESPONSE_REGRESSION", lambda: record_sponsor_event(
        accepted["ledger"], event_id="EV-SP-2", occurred_at="2026-09-13T10:11:00Z", submission_id="SUB-315-v1",
        submission_digest=r["submission_digest"], status="needs_changes", provider_event_key="changes-002",
        evidence_digest=sha("changes"), as_of=AS_OF,
    ))


def test_new_revision_requires_explicit_supersession_and_blocks_old_resend():
    old = sponsor(dispatch(registered()), "needs_changes")
    p2 = packet(evidence_sha=sha("artifact-v2"), head="b" * 40)
    new = register_candidate(
        old["ledger"], event_id="EV-REG-2", occurred_at="2026-09-13T10:20:00Z", submission_id="SUB-315-v2",
        packet=p2, artifact_revision=2, route_class="email_fallback", route_key="rustchain-sponsor-fallback",
        contract_digest="", as_of=AS_OF,
    )
    old_r = evaluate_submission(new["ledger"], "SUB-315-v1", as_of=AS_OF, follow_up_after_seconds=3600)
    new_r = evaluate_submission(new["ledger"], "SUB-315-v2", as_of=AS_OF, follow_up_after_seconds=3600)
    sup = supersede_candidate(
        new["ledger"], event_id="EV-SUP-1", occurred_at="2026-09-13T10:21:00Z",
        old_submission_id="SUB-315-v1", old_submission_digest=old_r["submission_digest"],
        new_submission_id="SUB-315-v2", new_submission_digest=new_r["submission_digest"],
        evidence_digest=sha("supersession"), as_of=AS_OF,
    )
    old_after = evaluate_submission(sup["ledger"], "SUB-315-v1", as_of=AS_OF, follow_up_after_seconds=3600)
    new_after = evaluate_submission(sup["ledger"], "SUB-315-v2", as_of=AS_OF, follow_up_after_seconds=3600)
    assert old_after["lifecycle_state"] == "SUPERSEDED"
    assert new_after["send_disposition"] == "READY_TO_SUBMIT"
    expect_code("SUPERSEDED_SUBMISSION_MUTATION", lambda: record_follow_up(
        sup["ledger"], event_id="EV-FU-OLD", occurred_at="2026-09-13T10:22:00Z", submission_id="SUB-315-v1",
        submission_digest=old_r["submission_digest"], route_class="email_fallback", provider_receipt_key="late-followup",
        evidence_digest=sha("late"), as_of=AS_OF,
    ))


def test_supersession_must_move_revision_forward_on_same_source():
    first = registered()
    p_other = packet(issue=316, evidence_sha=sha("artifact-316"), head="c" * 40)
    second = register_candidate(
        first["ledger"], event_id="EV-REG-2", occurred_at="2026-09-13T10:01:00Z", submission_id="SUB-316-v1",
        packet=p_other, artifact_revision=2, route_class="email_fallback", route_key="rustchain-sponsor-fallback",
        contract_digest="", as_of=AS_OF,
    )
    r1 = evaluate_submission(second["ledger"], "SUB-315-v1", as_of=AS_OF, follow_up_after_seconds=3600)
    r2 = evaluate_submission(second["ledger"], "SUB-316-v1", as_of=AS_OF, follow_up_after_seconds=3600)
    expect_code("SUPERSESSION_SOURCE_MISMATCH", lambda: supersede_candidate(
        second["ledger"], event_id="EV-SUP-1", occurred_at="2026-09-13T10:02:00Z", old_submission_id="SUB-315-v1",
        old_submission_digest=r1["submission_digest"], new_submission_id="SUB-316-v1", new_submission_digest=r2["submission_digest"],
        evidence_digest=sha("sup"), as_of=AS_OF,
    ))


def test_ledger_tamper_and_unknown_fields_fail_closed():
    reg = registered()
    tampered = json.loads(json.dumps(reg["ledger"]))
    tampered["events"][0]["artifact_revision"] = 9
    expect_code("LEDGER_DIGEST_MISMATCH", lambda: verify_ledger(tampered, as_of=AS_OF))
    extra = json.loads(json.dumps(reg["ledger"]))
    extra["extra"] = True
    expect_code("LEDGER_SHAPE_MISMATCH", lambda: verify_ledger(extra, as_of=AS_OF))


def test_bool_never_aliases_integer_policy_or_revision():
    expect_code("ARTIFACT_REVISION_INVALID", lambda: registered(artifact_revision=True))
    reg = registered()
    expect_code("FOLLOW_UP_POLICY_INVALID", lambda: evaluate_submission(reg["ledger"], "SUB-315-v1", as_of=AS_OF, follow_up_after_seconds=True))


def test_strict_json_loader_rejects_duplicate_keys():
    expect_code("DUPLICATE_JSON_KEY", lambda: strict_json_loads('{"schema":"x","schema":"y"}'))
    assert strict_json_loads('{"a":{"b":1},"c":2}') == {"a": {"b": 1}, "c": 2}


def test_receipt_is_deterministic_for_same_trusted_time_and_policy():
    sent = dispatch(registered())
    a = evaluate_submission(sent["ledger"], "SUB-315-v1", as_of="2026-09-13T11:00:00Z", follow_up_after_seconds=3600)
    b = evaluate_submission(sent["ledger"], "SUB-315-v1", as_of="2026-09-13T11:00:00Z", follow_up_after_seconds=3600)
    assert a == b
    assert a["receipt_sha256"] == b["receipt_sha256"]


def test_receipt_digest_changes_with_trusted_as_of_not_hidden_state():
    sent = dispatch(registered())
    a = evaluate_submission(sent["ledger"], "SUB-315-v1", as_of="2026-09-13T10:30:00Z", follow_up_after_seconds=3600)
    b = evaluate_submission(sent["ledger"], "SUB-315-v1", as_of="2026-09-13T11:30:00Z", follow_up_after_seconds=3600)
    assert a["receipt_sha256"] != b["receipt_sha256"]
    assert b["lifecycle_state"] == "FOLLOW_UP_DUE"


def _resign_packet(value):
    core = {k: v for k, v in value.items() if k != "packet_sha256"}
    value["packet_sha256"] = hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return value


def test_secret_shaped_packet_field_is_rejected_before_digest_trust():
    p = packet()
    p["apiToken"] = "not-a-real-secret"
    _resign_packet(p)
    expect_code("SECRET_SHAPED_FIELD", lambda: registered(packet_value=p))


def test_packet_pr_url_must_be_canonical_and_match_source_repository():
    p = packet()
    p["evidence"]["pull_request_url"] = "https://github.com/OtherOrg/other/pull/999"
    p["evidence"]["pull_request_repo"] = "OtherOrg/other"
    _resign_packet(p)
    expect_code("PACKET_PR_SOURCE_REPO_MISMATCH", lambda: registered(packet_value=p))

    p = packet()
    p["evidence"]["pull_request_url"] = "https://github.com/Scottcjn/rustchain-bounties/pull/0999"
    _resign_packet(p)
    expect_code("PACKET_PR_URL_INVALID", lambda: registered(packet_value=p))


def test_packet_pr_number_and_repo_must_match_canonical_url():
    p = packet()
    p["evidence"]["pull_request_number"] = 998
    _resign_packet(p)
    expect_code("PACKET_PR_NUMBER_INVALID", lambda: registered(packet_value=p))

    p = packet()
    p["evidence"]["pull_request_repo"] = "Scottcjn/not-the-repo"
    _resign_packet(p)
    expect_code("PACKET_PR_REPO_INVALID", lambda: registered(packet_value=p))


def test_ready_packet_cannot_hide_changed_path_outside_allowlist():
    p = packet()
    p["evidence"]["changed_paths"].append("unexpected/side-effect.py")
    _resign_packet(p)
    expect_code("PACKET_CHANGED_PATH_OUTSIDE_ALLOWLIST", lambda: registered(packet_value=p))


def test_ready_packet_cannot_lie_about_failed_tests_or_acceptance():
    p = packet()
    p["evidence"]["tests"][0]["outcome"] = "FAIL"
    _resign_packet(p)
    expect_code("PACKET_TEST_NOT_PASS", lambda: registered(packet_value=p))

    p = packet()
    p["evidence"]["acceptance_checks"][0]["status"] = "UNKNOWN"
    _resign_packet(p)
    expect_code("PACKET_ACCEPTANCE_NOT_PASS", lambda: registered(packet_value=p))


def test_packet_reward_must_be_finite_positive_decimal_text():
    for reward in ("0", "-1", "NaN", "Infinity", " 45"):
        p = packet(reward=reward)
        expect_code("PACKET_REWARD_INVALID", lambda p=p: registered(packet_value=p))


def test_packet_paths_must_be_canonical_unique_relative_paths():
    p = packet()
    p["evidence"]["changed_paths"] = ["deliverables/asset.md", "deliverables/asset.md"]
    _resign_packet(p)
    expect_code("PACKET_PATH_DUPLICATE", lambda: registered(packet_value=p))

    p = packet()
    p["evidence"]["changed_paths"] = ["../asset.md"]
    _resign_packet(p)
    expect_code("PACKET_PATH_INVALID", lambda: registered(packet_value=p))
