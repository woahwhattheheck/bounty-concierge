# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from concierge import revenue_settlement as rs
from concierge import settlement_registry as sr


def closeout(*, pr=7, amount="10"):
    return {
        "repo": "Sponsor/project",
        "pr": pr,
        "state": "MERGED",
        "currency": "RTC",
        "advertised_amount": amount,
        "cash_status": "not_inferred",
        "merged_at": "2026-09-12T23:59:59Z",
    }


def payment(tx_hash, amount, *, second=1):
    return {
        "type": "transfer_in",
        "amount": amount,
        "from": "treasury",
        "timestamp": f"2026-09-13T00:00:{second:02d}Z",
        "tx_hash": tx_hash,
    }


def legacy_payment(tx_hash, amount):
    return {
        "amount_rtc": amount,
        "from": "treasury",
        "to": "alice",
        "created_at": "2026-09-13T00:00:01Z",
        "tx_hash": tx_hash,
    }


def bind(item, *rows, wallet="alice"):
    return {
        "repo": item["repo"],
        "pr": item["pr"],
        "history_sha256s": [
            rs.history_row_sha256(row, wallet=wallet) for row in rows
        ],
    }


def apply(state, item, history, *selected):
    selected = selected or tuple(history)
    return sr.reconcile_and_apply(
        state,
        item,
        history,
        bind(item, *selected),
        wallet="alice",
        history_wallet="alice",
        history_source="captured_wallet",
        expected_state_sha256=state["state_sha256"],
    )


def test_genesis_is_deterministic_empty_and_authority_false():
    first = sr.new_registry(wallet="alice", history_source="captured_wallet")
    second = sr.new_registry(wallet="alice", history_source="captured_wallet")
    assert first == second
    assert first["generation"] == 0
    assert first["claims"] == []
    assert first["transactions"] == []
    assert all(value is False for value in first["authority"].values())
    assert sr.verify_registry(first) == first


def test_first_partial_claim_is_custodied_with_receipt():
    state = sr.new_registry(wallet="alice", history_source="captured_wallet")
    row = payment("tx-1", "4")
    updated, receipt = apply(state, closeout(amount="10"), [row])
    assert updated["generation"] == 1
    assert updated["previous_state_sha256"] == state["state_sha256"]
    assert updated["claims"][0]["verified_amount"] == "4"
    assert updated["claims"][0]["cash_status"] == "partially_verified"
    assert updated["transactions"][0]["transaction_id"] == "tx-1"
    assert receipt["changed"] is True
    assert sr.verify_receipt(receipt, registry=updated) == receipt


def test_exact_replay_is_idempotent_and_does_not_advance_generation():
    state = sr.new_registry(wallet="alice", history_source="captured_wallet")
    row = payment("tx-1", "4")
    updated, _ = apply(state, closeout(amount="10"), [row])
    replayed, receipt = apply(updated, closeout(amount="10"), [row])
    assert replayed == updated
    assert receipt["changed"] is False
    assert receipt["previous_state_sha256"] == updated["state_sha256"]
    assert receipt["state_sha256"] == updated["state_sha256"]


def test_same_transaction_cannot_be_recycled_into_another_claim():
    state = sr.new_registry(wallet="alice", history_source="captured_wallet")
    row = payment("tx-1", "4")
    updated, _ = apply(state, closeout(pr=7, amount="10"), [row])
    with pytest.raises(sr.SettlementRegistryConflict, match="already bound"):
        apply(updated, closeout(pr=8, amount="10"), [row])


def test_same_transaction_identity_cannot_change_history_or_amount():
    state = sr.new_registry(wallet="alice", history_source="captured_wallet")
    first = payment("tx-1", "4")
    updated, _ = apply(state, closeout(amount="10"), [first])
    changed = payment("tx-1", "5", second=2)
    with pytest.raises(sr.SettlementRegistryConflict, match="already bound"):
        apply(updated, closeout(amount="10"), [changed])


def test_partial_claim_can_advance_monotonically_to_paid():
    state = sr.new_registry(wallet="alice", history_source="captured_wallet")
    first = payment("tx-1", "4", second=1)
    second = payment("tx-2", "6", second=2)
    partial, _ = apply(state, closeout(amount="10"), [first], first)
    paid, receipt = apply(
        partial,
        closeout(amount="10"),
        [first, second],
        first,
        second,
    )
    assert paid["generation"] == 2
    assert paid["claims"][0]["verified_amount"] == "10"
    assert paid["claims"][0]["cash_status"] == "verified_paid"
    assert paid["claims"][0]["transaction_ids"] == ["tx-1", "tx-2"]
    assert receipt["cash_status"] == "verified_paid"


def test_later_run_cannot_omit_previously_custodied_transaction():
    state = sr.new_registry(wallet="alice", history_source="captured_wallet")
    first = payment("tx-1", "4", second=1)
    second = payment("tx-2", "2", second=2)
    advanced, _ = apply(
        state,
        closeout(amount="10"),
        [first, second],
        first,
        second,
    )
    with pytest.raises(sr.SettlementRegistryConflict, match="rolled back"):
        apply(advanced, closeout(amount="10"), [first], first)


def test_stale_expected_state_digest_fails_before_update():
    state = sr.new_registry(wallet="alice", history_source="captured_wallet")
    row = payment("tx-1", "10")
    item = closeout(amount="10")
    with pytest.raises(sr.SettlementRegistryConflict, match="expected digest"):
        sr.reconcile_and_apply(
            state,
            item,
            [row],
            bind(item, row),
            wallet="alice",
            history_wallet="alice",
            history_source="captured_wallet",
            expected_state_sha256="0" * 64,
        )


def test_registry_wallet_and_history_source_are_process_bound():
    state = sr.new_registry(wallet="alice", history_source="captured_wallet")
    row = payment("tx-1", "10")
    item = closeout(amount="10")
    with pytest.raises(sr.SettlementRegistryConflict, match="wallet"):
        sr.reconcile_and_apply(
            state,
            item,
            [row],
            bind(item, row),
            wallet="bob",
            history_wallet="bob",
            history_source="captured_wallet",
            expected_state_sha256=state["state_sha256"],
        )
    with pytest.raises(sr.SettlementRegistryConflict, match="history source"):
        sr.reconcile_and_apply(
            state,
            item,
            [row],
            bind(item, row),
            wallet="alice",
            history_wallet="alice",
            history_source="other_capture",
            expected_state_sha256=state["state_sha256"],
        )


def test_legacy_typeless_evidence_is_not_given_durable_transaction_authority():
    state = sr.new_registry(wallet="alice", history_source="captured_wallet")
    row = legacy_payment("legacy-1", "4")
    item = closeout(amount="10")
    with pytest.raises(sr.SettlementRegistryError, match="canonical transfer_in"):
        apply(state, item, [row], row)


def test_state_and_receipt_tamper_fail_digest_verification():
    state = sr.new_registry(wallet="alice", history_source="captured_wallet")
    row = payment("tx-1", "4")
    updated, receipt = apply(state, closeout(amount="10"), [row])

    bad_state = copy.deepcopy(updated)
    bad_state["claims"][0]["verified_amount"] = "3"
    with pytest.raises(sr.SettlementRegistryError, match="digest mismatch"):
        sr.verify_registry(bad_state)

    bad_receipt = copy.deepcopy(receipt)
    bad_receipt["verified_amount"] = "3"
    with pytest.raises(sr.SettlementRegistryError, match="digest mismatch"):
        sr.verify_receipt(bad_receipt)


def test_resealed_bool_generation_and_authority_escalation_still_fail_schema():
    state = sr.new_registry(wallet="alice", history_source="captured_wallet")
    body = {
        key: copy.deepcopy(value)
        for key, value in state.items()
        if key != "state_sha256"
    }
    body["generation"] = False
    with pytest.raises(sr.SettlementRegistryError, match="generation"):
        sr.verify_registry(sr._seal_state(body))

    body = {
        key: copy.deepcopy(value)
        for key, value in state.items()
        if key != "state_sha256"
    }
    body["authority"]["revenue_recognition"] = True
    with pytest.raises(sr.SettlementRegistryError, match="authority"):
        sr.verify_registry(sr._seal_state(body))


def test_strict_json_rejects_duplicate_keys_nonfinite_and_floats():
    with pytest.raises(sr.SettlementRegistryError, match="duplicate"):
        sr.strict_json_loads('{"a":1,"a":2}')
    with pytest.raises(sr.SettlementRegistryError, match="non-finite"):
        sr.strict_json_loads('{"a":NaN}')
    with pytest.raises(sr.SettlementRegistryError, match="floating"):
        sr.strict_json_loads('{"a":1.5}')


def test_file_registry_commits_and_reload_under_cross_process_lock(tmp_path):
    target = tmp_path / "settlements.json"
    state = sr.initialize_registry_file(
        target,
        wallet="alice",
        history_source="captured_wallet",
    )
    row = payment("tx-1", "4")
    item = closeout(amount="10")
    updated, receipt = sr.commit_claim_file(
        target,
        item,
        [row],
        bind(item, row),
        wallet="alice",
        history_wallet="alice",
        history_source="captured_wallet",
        expected_state_sha256=state["state_sha256"],
    )
    assert sr.load_registry_file(target) == updated
    assert sr.verify_receipt(receipt, registry=updated) == receipt
    assert not Path(str(target) + ".lock").exists()


def test_stale_file_cas_leaves_old_state_and_releases_lock(tmp_path):
    target = tmp_path / "settlements.json"
    state = sr.initialize_registry_file(
        target,
        wallet="alice",
        history_source="captured_wallet",
    )
    row = payment("tx-1", "4")
    item = closeout(amount="10")
    with pytest.raises(sr.SettlementRegistryConflict):
        sr.commit_claim_file(
            target,
            item,
            [row],
            bind(item, row),
            wallet="alice",
            history_wallet="alice",
            history_source="captured_wallet",
            expected_state_sha256="0" * 64,
        )
    assert sr.load_registry_file(target) == state
    assert not Path(str(target) + ".lock").exists()


def test_postrename_failure_preserves_ambiguity_hold(tmp_path, monkeypatch):
    target = tmp_path / "settlements.json"
    state = sr.initialize_registry_file(
        target,
        wallet="alice",
        history_source="captured_wallet",
    )
    row = payment("tx-1", "4")
    item = closeout(amount="10")

    def fail_parent_fsync(path):
        raise OSError("injected parent fsync failure")

    monkeypatch.setattr(sr, "_fsync_parent", fail_parent_fsync)
    with pytest.raises(sr.SettlementRegistryAmbiguousCommit):
        sr.commit_claim_file(
            target,
            item,
            [row],
            bind(item, row),
            wallet="alice",
            history_wallet="alice",
            history_source="captured_wallet",
            expected_state_sha256=state["state_sha256"],
        )

    lock = Path(str(target) + ".lock")
    assert lock.exists()
    marker = sr._read_lock_marker(lock)
    assert marker["status"] == "ambiguous"
    with pytest.raises(sr.SettlementRegistryBusy, match="locked"):
        sr.load_registry_file(target)

    audit = sr.audit_registry_file(target)
    assert audit["live_authority"] is False
    assert audit["lock_present"] is True
    assert audit["state"]["generation"] == 1


def test_ambiguity_marker_requires_exact_operator_acceptance(tmp_path, monkeypatch):
    target = tmp_path / "settlements.json"
    state = sr.initialize_registry_file(
        target,
        wallet="alice",
        history_source="captured_wallet",
    )
    row = payment("tx-1", "4")
    item = closeout(amount="10")
    monkeypatch.setattr(
        sr,
        "_fsync_parent",
        lambda path: (_ for _ in ()).throw(OSError("injected failure")),
    )
    with pytest.raises(sr.SettlementRegistryAmbiguousCommit):
        sr.commit_claim_file(
            target,
            item,
            [row],
            bind(item, row),
            wallet="alice",
            history_wallet="alice",
            history_source="captured_wallet",
            expected_state_sha256=state["state_sha256"],
        )
    audit = sr.audit_registry_file(target)
    accepted_sha = audit["state"]["state_sha256"]
    with pytest.raises(sr.SettlementRegistryConflict):
        sr.clear_reconciled_lock(
            target,
            expected_state_sha256="0" * 64,
        )
    cleared = sr.clear_reconciled_lock(
        target,
        expected_state_sha256=accepted_sha,
    )
    assert cleared["state_sha256"] == accepted_sha
    assert sr.load_registry_file(target)["state_sha256"] == accepted_sha


def test_live_or_stale_writing_lock_is_never_auto_cleared(tmp_path):
    target = tmp_path / "settlements.json"
    state = sr.initialize_registry_file(
        target,
        wallet="alice",
        history_source="captured_wallet",
    )
    lock = sr._acquire_lock(target)
    try:
        with pytest.raises(sr.SettlementRegistryBusy, match="not an ambiguity"):
            sr.clear_reconciled_lock(
                target,
                expected_state_sha256=state["state_sha256"],
            )
    finally:
        lock.unlink()


def test_registry_file_loader_rejects_duplicate_key_json(tmp_path):
    target = tmp_path / "settlements.json"
    target.write_text(
        '{"schema_version":"x","schema_version":"y"}',
        encoding="utf-8",
    )
    with pytest.raises(sr.SettlementRegistryError, match="duplicate"):
        sr.load_registry_file(target)
