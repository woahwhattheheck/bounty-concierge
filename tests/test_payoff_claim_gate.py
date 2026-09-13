# SPDX-License-Identifier: MIT
"""Exact payoff-path bundles must bind one READY item to one GitHub claim."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from concierge import payoff_claim_gate as gate


def _packet(*rows):
    return {
        "schema": "payoff-path-gate/v1",
        "evaluated_at_utc": "2026-09-13T15:00:00.000Z",
        "source_document_sha256": "a" * 64,
        "summary": {},
        "results": list(rows),
        "authority": "OWNER_REVIEW_ONLY_NO_EXTERNAL_ACTION",
    }


def _row(
    *,
    url="https://github.com/acme/widget/issues/42",
    mechanism="BOUNTY",
    event="SUBMIT_WORK",
    state="READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW",
    remaining=90,
    work_id="work-42",
):
    return {
        "work_id": work_id,
        "opportunity_id": "acme-widget-42",
        "state": state,
        "reasons": ["PAYOFF_PATH_CURRENT_AND_FREE_WORK_WITHIN_CAP"],
        "free_work_budget_minutes": 120,
        "free_work_spent_minutes": 30,
        "free_work_remaining_minutes": remaining,
        "payoff_path": {
            "mechanism": mechanism,
            "value": {"kind": "FIXED", "currency": "USD", "amount_minor": 9000},
            "canonical_source_url": url,
            "source_evidence_ref": "source:issue-42",
            "source_evidence_sha256": "b" * 64,
            "source_observed_at_utc": "2026-09-13T14:00:00.000Z",
            "conversion_event": event,
            "conversion_due_at_utc": "2026-09-20T00:00:00.000Z",
            "conversion_evidence_ref": "conversion:deadline",
            "conversion_evidence_sha256": "c" * 64,
        },
    }


def _bundle(root: Path, packet=None):
    root.mkdir()
    (root / "work.json").write_text(json.dumps({"schema": "payoff-path-work/v1", "work_items": []}), encoding="utf-8")
    (root / "packet.json").write_text(json.dumps(packet or _packet(_row())), encoding="utf-8")
    (root / "review.md").write_text("# verified\n", encoding="utf-8")
    (root / "receipt.json").write_text(json.dumps({"packet_sha256": "d" * 64}), encoding="utf-8")
    return root


def test_ready_exact_bounty_target_returns_safe_prerequisite_proof(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(
        gate,
        "verify_gate",
        lambda document, packet, markdown, receipt, trusted_now=None: seen.append(
            (document, packet, markdown, receipt, trusted_now)
        )
        or True,
    )
    root = _bundle(tmp_path / "bundle")

    proof = gate.verify_claim_payoff_bundle(
        "acme/widget", 42, root, trusted_now="2026-09-13T15:01:00.000Z"
    )

    assert proof == {
        "schema": "payoff-claim-proof/v1",
        "repo": "acme/widget",
        "issue": 42,
        "canonical_issue_url": "https://github.com/acme/widget/issues/42",
        "work_id": "work-42",
        "opportunity_id": "acme-widget-42",
        "state": "READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW",
        "free_work_remaining_minutes": 90,
        "mechanism": "BOUNTY",
        "value": {"kind": "FIXED", "currency": "USD", "amount_minor": 9000},
        "conversion_event": "SUBMIT_WORK",
        "conversion_due_at_utc": "2026-09-20T00:00:00.000Z",
        "packet_evaluated_at_utc": "2026-09-13T15:00:00.000Z",
        "source_document_sha256": "a" * 64,
        "packet_sha256": "d" * 64,
        "authority": "EXPLICIT_OWNER_CLI_PREREQUISITE_ONLY_NO_EXTERNAL_ACTION",
    }
    assert seen[0][-1] == "2026-09-13T15:01:00.000Z"


def test_repository_identity_is_case_insensitive_but_issue_must_match(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "verify_gate", lambda *a, **k: True)
    root = _bundle(
        tmp_path / "bundle",
        _packet(_row(url="https://github.com/Acme/Widget/issues/42")),
    )
    assert gate.verify_claim_payoff_bundle("acme/widget", 42, root)["work_id"] == "work-42"
    with pytest.raises(gate.ClaimPayoffError) as caught:
        gate.verify_claim_payoff_bundle("acme/widget", 43, root)
    assert caught.value.code == "CLAIM_TARGET_NOT_IN_BUNDLE"


def test_query_fragment_noncanonical_or_other_host_does_not_bind(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "verify_gate", lambda *a, **k: True)
    urls = [
        "https://github.com/acme/widget/issues/42?claim=1",
        "https://example.com/acme/widget/issues/42",
        "https://github.com/acme/widget/pull/42",
        "https://github.com/acme//widget/issues/42",
        "https://github.com/acme/widget/issues/42/",
        "https://github.com:443/acme/widget/issues/42",
    ]
    for index, url in enumerate(urls):
        root = _bundle(tmp_path / f"bundle-{index}", _packet(_row(url=url)))
        with pytest.raises(gate.ClaimPayoffError) as caught:
            gate.verify_claim_payoff_bundle("acme/widget", 42, root)
        assert caught.value.code == "CLAIM_TARGET_NOT_IN_BUNDLE"


def test_zero_or_multiple_target_rows_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "verify_gate", lambda *a, **k: True)
    root = _bundle(tmp_path / "none", _packet(_row(url="https://github.com/acme/other/issues/42")))
    with pytest.raises(gate.ClaimPayoffError) as caught:
        gate.verify_claim_payoff_bundle("acme/widget", 42, root)
    assert caught.value.code == "CLAIM_TARGET_NOT_IN_BUNDLE"

    root = _bundle(tmp_path / "many", _packet(_row(), _row(work_id="work-42-b")))
    with pytest.raises(gate.ClaimPayoffError) as caught:
        gate.verify_claim_payoff_bundle("acme/widget", 42, root)
    assert caught.value.code == "AMBIGUOUS_CLAIM_TARGET"


@pytest.mark.parametrize(
    ("row", "code"),
    [
        (_row(mechanism="COMPETITION_PRIZE", event="ENTER_COMPETITION"), "WRONG_COMPENSATION_MECHANISM"),
        (_row(event="SEND_PAID_OFFER"), "WRONG_COMPENSATION_MECHANISM"),
        (_row(state="HOLD_STALE_OR_INVALID"), "PAYOFF_PATH_NOT_READY"),
        (_row(state="STOP_UNPAID_WORK", remaining=0), "PAYOFF_PATH_NOT_READY"),
        (_row(remaining=0), "FREE_WORK_BUDGET_EXHAUSTED"),
    ],
)
def test_wrong_mechanism_state_or_budget_is_blocked(tmp_path, monkeypatch, row, code):
    monkeypatch.setattr(gate, "verify_gate", lambda *a, **k: True)
    root = _bundle(tmp_path / code, _packet(row))
    with pytest.raises(gate.ClaimPayoffError) as caught:
        gate.verify_claim_payoff_bundle("acme/widget", 42, root)
    assert caught.value.code == code


def test_exact_gate_failure_is_sanitized(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise gate.PayoffPathError("hostile source text DO NOT ECHO")

    monkeypatch.setattr(gate, "verify_gate", fail)
    root = _bundle(tmp_path / "bundle")
    with pytest.raises(gate.ClaimPayoffError) as caught:
        gate.verify_claim_payoff_bundle("acme/widget", 42, root)
    assert caught.value.code == "INVALID_GATE_BUNDLE"
    assert "DO NOT ECHO" not in str(caught.value)


def test_missing_member_and_non_utf8_member_fail_before_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "verify_gate", lambda *a, **k: pytest.fail("unsafe bundle reached verifier"))
    root = _bundle(tmp_path / "missing")
    (root / "receipt.json").unlink()
    with pytest.raises(gate.ClaimPayoffError) as caught:
        gate.verify_claim_payoff_bundle("acme/widget", 42, root)
    assert caught.value.code == "BUNDLE_MEMBER_UNAVAILABLE"

    root = _bundle(tmp_path / "binary")
    (root / "review.md").write_bytes(b"\xff")
    with pytest.raises(gate.ClaimPayoffError) as caught:
        gate.verify_claim_payoff_bundle("acme/widget", 42, root)
    assert caught.value.code == "INVALID_BUNDLE_ENCODING"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")
def test_bundle_and_member_symlinks_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "verify_gate", lambda *a, **k: pytest.fail("symlink reached verifier"))
    real = _bundle(tmp_path / "real")
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(gate.ClaimPayoffError) as caught:
        gate.verify_claim_payoff_bundle("acme/widget", 42, linked)
    assert caught.value.code == "UNSAFE_BUNDLE_DIRECTORY"

    root = _bundle(tmp_path / "member")
    target = root / "target.json"
    target.write_text("{}", encoding="utf-8")
    (root / "packet.json").unlink()
    (root / "packet.json").symlink_to(target)
    with pytest.raises(gate.ClaimPayoffError) as caught:
        gate.verify_claim_payoff_bundle("acme/widget", 42, root)
    assert caught.value.code in {"BUNDLE_MEMBER_UNAVAILABLE", "UNSAFE_BUNDLE_FILE"}


def test_invalid_repo_issue_and_verifier_false_fail_closed(tmp_path, monkeypatch):
    root = _bundle(tmp_path / "bundle")
    monkeypatch.setattr(gate, "verify_gate", lambda *a, **k: True)
    for repo, issue in [("widget", 42), ("acme/widget/extra", 42), ("acme/widget", 0), ("acme/widget", True)]:
        with pytest.raises(gate.ClaimPayoffError) as caught:
            gate.verify_claim_payoff_bundle(repo, issue, root)
        assert caught.value.code == "INVALID_CLAIM_TARGET"

    monkeypatch.setattr(gate, "verify_gate", lambda *a, **k: False)
    with pytest.raises(gate.ClaimPayoffError) as caught:
        gate.verify_claim_payoff_bundle("acme/widget", 42, root)
    assert caught.value.code == "INVALID_GATE_BUNDLE"


def test_real_gate_bundle_compiles_verifies_and_binds_exact_target(tmp_path):
    from concierge.payoff_path_gate import compile_gate

    as_of = "2026-09-13T15:00:00.000Z"
    document = {
        "schema": "payoff-path-work/v1",
        "work_items": [
            {
                "work_id": "work-acme-widget-42",
                "opportunity_id": "github:acme/widget:42",
                "started_at_utc": "2026-09-13T13:00:00.000Z",
                "free_work_budget_minutes": 180,
                "free_work_spent_minutes": 30,
                "payoff_path": {
                    "mechanism": "BOUNTY",
                    "value": {
                        "kind": "FIXED",
                        "currency": "USD",
                        "amount_minor": 9000,
                    },
                    "source": {
                        "canonical_url": "https://github.com/acme/widget/issues/42",
                        "evidence_ref": "github:issue-42-terms",
                        "evidence_sha256": "a" * 64,
                        "observed_at_utc": "2026-09-13T14:00:00.000Z",
                        "max_age_days": 7,
                    },
                    "conversion": {
                        "event": "SUBMIT_WORK",
                        "due_at_utc": "2026-09-20T12:00:00.000Z",
                        "evidence_ref": "github:issue-42-deadline",
                        "evidence_sha256": "b" * 64,
                    },
                },
            }
        ],
    }
    packet, markdown, receipt = compile_gate(document, as_of)
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "work.json").write_text(json.dumps(document), encoding="utf-8")
    (root / "packet.json").write_text(json.dumps(packet), encoding="utf-8")
    (root / "review.md").write_text(markdown, encoding="utf-8")
    (root / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")

    proof = gate.verify_claim_payoff_bundle(
        "acme/widget",
        42,
        root,
        trusted_now="2026-09-13T15:01:00.000Z",
    )

    assert proof["work_id"] == "work-acme-widget-42"
    assert proof["free_work_remaining_minutes"] == 150
    assert proof["mechanism"] == "BOUNTY"
