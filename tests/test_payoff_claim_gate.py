# SPDX-License-Identifier: MIT
"""Exact payoff bundles must bind one current READY item to one GitHub claim."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from concierge import payoff_claim_gate as gate


def _packet(*rows, generation=0):
    return {
        "schema": "payoff-path-gate/v3",
        "evaluated_at_utc": "2026-09-14T23:00:00.000Z",
        "source_document_sha256": "a" * 64,
        "continuity": {"generation": generation},
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
            "source_observed_at_utc": "2026-09-14T22:00:00.000Z",
            "conversion_event": event,
            "conversion_due_at_utc": "2026-09-20T00:00:00.000Z",
            "conversion_evidence_ref": "conversion:deadline",
            "conversion_evidence_sha256": "c" * 64,
        },
    }


def _bundle(root: Path, packet=None, *, previous=None):
    root.mkdir()
    (root / "work.json").write_text(
        json.dumps({"schema": "payoff-path-work/v3", "continuity": {}, "work_items": []}),
        encoding="utf-8",
    )
    (root / "packet.json").write_text(
        json.dumps(packet or _packet(_row())), encoding="utf-8"
    )
    (root / "review.md").write_text("# verified\n", encoding="utf-8")
    (root / "receipt.json").write_text(
        json.dumps({"packet_sha256": "d" * 64}), encoding="utf-8"
    )
    if previous is not None:
        (root / "previous-receipt.json").write_text(
            json.dumps(previous), encoding="utf-8"
        )
    return root


def test_ready_exact_bounty_target_returns_safe_prerequisite_proof(tmp_path, monkeypatch):
    seen = []

    def verify(document, packet, markdown, receipt, *, trusted_now=None, previous_receipt=None):
        seen.append((document, packet, markdown, receipt, trusted_now, previous_receipt))
        return True

    monkeypatch.setattr(gate, "verify_gate", verify)
    root = _bundle(tmp_path / "bundle")

    proof = gate.verify_claim_payoff_bundle(
        "acme/widget", 42, root, trusted_now="2026-09-14T23:01:00.000Z"
    )

    assert proof["schema"] == "payoff-claim-proof/v2"
    assert proof["repo"] == "acme/widget"
    assert proof["issue"] == 42
    assert proof["work_id"] == "work-42"
    assert proof["state"] == "READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW"
    assert proof["free_work_remaining_minutes"] == 90
    assert proof["mechanism"] == "BOUNTY"
    assert proof["conversion_event"] == "SUBMIT_WORK"
    assert proof["packet_schema"] == "payoff-path-gate/v3"
    assert proof["continuity_generation"] == 0
    assert proof["authority"] == "EXPLICIT_OWNER_CLI_PREREQUISITE_ONLY_NO_EXTERNAL_ACTION"
    assert seen[0][-2] == "2026-09-14T23:01:00.000Z"
    assert seen[0][-1] is None


def test_previous_receipt_is_parsed_and_passed_to_canonical_verifier(tmp_path, monkeypatch):
    predecessor = {
        "schema": "payoff-path-gate-receipt/v3",
        "source_document_sha256": "1" * 64,
        "packet_sha256": "2" * 64,
        "markdown_sha256": "3" * 64,
        "continuity": {"generation": 0},
    }
    seen = []

    def verify(document, packet, markdown, receipt, *, trusted_now=None, previous_receipt=None):
        seen.append(previous_receipt)
        return True

    monkeypatch.setattr(gate, "verify_gate", verify)
    root = _bundle(
        tmp_path / "bundle",
        _packet(_row(), generation=1),
        previous=predecessor,
    )
    proof = gate.verify_claim_payoff_bundle("acme/widget", 42, root)

    assert seen == [predecessor]
    assert proof["continuity_generation"] == 1


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


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/widget/issues/42?claim=1",
        "https://example.com/acme/widget/issues/42",
        "https://github.com/acme/widget/pull/42",
        "https://github.com/acme//widget/issues/42",
        "https://github.com/acme/widget/issues/42/",
        "https://github.com:443/acme/widget/issues/42",
    ],
)
def test_noncanonical_issue_urls_do_not_bind(tmp_path, monkeypatch, url):
    monkeypatch.setattr(gate, "verify_gate", lambda *a, **k: True)
    root = _bundle(tmp_path / "bundle", _packet(_row(url=url)))
    with pytest.raises(gate.ClaimPayoffError) as caught:
        gate.verify_claim_payoff_bundle("acme/widget", 42, root)
    assert caught.value.code == "CLAIM_TARGET_NOT_IN_BUNDLE"


def test_zero_or_multiple_target_rows_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "verify_gate", lambda *a, **k: True)
    root = _bundle(
        tmp_path / "none",
        _packet(_row(url="https://github.com/acme/other/issues/42")),
    )
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
        (
            _row(mechanism="COMPETITION_PRIZE", event="ENTER_COMPETITION"),
            "WRONG_COMPENSATION_MECHANISM",
        ),
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


def test_invalid_previous_receipt_json_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(
        gate,
        "verify_gate",
        lambda *a, **k: pytest.fail("invalid predecessor reached verifier"),
    )
    root = _bundle(tmp_path / "bundle")
    (root / "previous-receipt.json").write_text("{", encoding="utf-8")
    with pytest.raises(gate.ClaimPayoffError) as caught:
        gate.verify_claim_payoff_bundle("acme/widget", 42, root)
    assert caught.value.code == "INVALID_GATE_BUNDLE"


def test_invalid_repo_issue_and_verifier_false_fail_closed(tmp_path, monkeypatch):
    root = _bundle(tmp_path / "bundle")
    monkeypatch.setattr(gate, "verify_gate", lambda *a, **k: True)
    for repo, issue in [
        ("widget", 42),
        ("acme/widget/extra", 42),
        ("acme/widget", 0),
        ("acme/widget", True),
    ]:
        with pytest.raises(gate.ClaimPayoffError) as caught:
            gate.verify_claim_payoff_bundle(repo, issue, root)
        assert caught.value.code == "INVALID_CLAIM_TARGET"

    monkeypatch.setattr(gate, "verify_gate", lambda *a, **k: False)
    with pytest.raises(gate.ClaimPayoffError) as caught:
        gate.verify_claim_payoff_bundle("acme/widget", 42, root)
    assert caught.value.code == "INVALID_GATE_BUNDLE"
