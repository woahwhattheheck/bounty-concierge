# SPDX-License-Identifier: MIT
"""Installed claim path must consume canonical bounty preflight."""

from __future__ import annotations

import json

import pytest

from concierge import entrypoint


def _result(*, disposition="ACTIONABLE", attempts=1, open_prs=1, reasons=None):
    reasons = reasons or []
    return {
        "repo": "acme/widget",
        "number": 42,
        "attempt_count": attempts,
        "canonical_audit": {
            "open_pr_count": open_prs,
            # Hostile source text must never escape blocked output.
            "maintainer_expiry_comments": [{"body": "DO NOT ECHO THIS SOURCE"}],
        },
        "qualification": {
            "disposition": disposition,
            "dispatch": disposition == "ACTIONABLE",
            "reason_codes": reasons,
            "reasons": [
                {"code": code, "severity": disposition, "message": "safe"}
                for code in reasons
            ],
            "signals": {"private_context_signal_types": []},
        },
    }


def test_actionable_claim_runs_preflight_then_existing_cli(monkeypatch):
    seen = []
    monkeypatch.setattr(
        entrypoint,
        "preflight_bounty",
        lambda repo, issue: seen.append((repo, issue)) or _result(),
    )
    monkeypatch.setattr(entrypoint, "_cli_main", lambda: seen.append("cli"))
    monkeypatch.setattr(
        entrypoint.sys,
        "argv",
        ["concierge", "claim", "--repo", "acme/widget", "--issue", "42", "--wallet", "alice"],
    )

    entrypoint.main()

    assert seen == [("acme/widget", 42), "cli"]


def test_short_repo_is_normalized_before_preflight(monkeypatch):
    seen = []
    monkeypatch.setattr(
        entrypoint,
        "preflight_bounty",
        lambda repo, issue: seen.append((repo, issue)) or _result(),
    )
    monkeypatch.setattr(entrypoint, "_cli_main", lambda: None)
    monkeypatch.setattr(
        entrypoint.sys,
        "argv",
        ["concierge", "claim", "--repo", "widget", "--issue=42", "--wallet", "alice"],
    )

    entrypoint.main()

    assert seen == [("Scottcjn/widget", 42)]


def test_hold_blocks_before_cli_and_uses_exit_two(monkeypatch, capsys):
    monkeypatch.setattr(
        entrypoint,
        "preflight_bounty",
        lambda *a: _result(disposition="HOLD", attempts=5, open_prs=2, reasons=["SATURATED_COMPETITION"]),
    )
    monkeypatch.setattr(
        entrypoint,
        "_cli_main",
        lambda: pytest.fail("blocked claim reached primary CLI"),
    )
    monkeypatch.setattr(
        entrypoint.sys,
        "argv",
        ["concierge", "claim", "--issue", "42", "--wallet", "alice"],
    )

    with pytest.raises(SystemExit) as caught:
        entrypoint.main()

    assert caught.value.code == 2
    stderr = capsys.readouterr().err
    assert "SATURATED_COMPETITION" in stderr
    assert "attempts=5" in stderr
    assert "DO NOT ECHO" not in stderr


def test_reject_json_is_safe_and_uses_exit_three(monkeypatch, capsys):
    monkeypatch.setattr(
        entrypoint,
        "preflight_bounty",
        lambda *a: _result(disposition="REJECT", reasons=["ALREADY_REWARDED"]),
    )
    monkeypatch.setattr(
        entrypoint,
        "_cli_main",
        lambda: pytest.fail("blocked claim reached primary CLI"),
    )
    monkeypatch.setattr(
        entrypoint.sys,
        "argv",
        ["concierge", "--json", "claim", "--issue", "42", "--wallet", "alice"],
    )

    with pytest.raises(SystemExit) as caught:
        entrypoint.main()

    assert caught.value.code == 3
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert payload["error"] == "claim_blocked"
    assert payload["qualification"]["reason_codes"] == ["ALREADY_REWARDED"]
    assert "DO NOT ECHO" not in stdout


def test_dry_run_and_help_skip_network_preflight(monkeypatch):
    monkeypatch.setattr(
        entrypoint,
        "preflight_bounty",
        lambda *a: pytest.fail("preview/help must stay non-network"),
    )
    calls = []
    monkeypatch.setattr(entrypoint, "_cli_main", lambda: calls.append("cli"))

    monkeypatch.setattr(
        entrypoint.sys,
        "argv",
        ["concierge", "claim", "--issue", "42", "--wallet", "alice", "--dry-run"],
    )
    entrypoint.main()
    monkeypatch.setattr(entrypoint.sys, "argv", ["concierge", "claim", "--help"])
    entrypoint.main()

    assert calls == ["cli", "cli"]


def test_preflight_provider_failure_fails_closed(monkeypatch, capsys):
    monkeypatch.setattr(
        entrypoint,
        "preflight_bounty",
        lambda *a: (_ for _ in ()).throw(entrypoint.BountyPreflightError("provider down")),
    )
    monkeypatch.setattr(
        entrypoint,
        "_cli_main",
        lambda: pytest.fail("unqualified claim reached primary CLI"),
    )
    monkeypatch.setattr(
        entrypoint.sys,
        "argv",
        ["concierge", "claim", "--issue", "42", "--wallet", "alice"],
    )

    with pytest.raises(SystemExit) as caught:
        entrypoint.main()

    assert caught.value.code == 2
    assert "claim preflight unavailable" in capsys.readouterr().err


def test_malformed_preflight_result_fails_closed(monkeypatch):
    monkeypatch.setattr(entrypoint, "preflight_bounty", lambda *a: {"qualification": {}})
    monkeypatch.setattr(
        entrypoint,
        "_cli_main",
        lambda: pytest.fail("malformed preflight reached primary CLI"),
    )
    monkeypatch.setattr(
        entrypoint.sys,
        "argv",
        ["concierge", "claim", "--issue", "42", "--wallet", "alice"],
    )

    with pytest.raises(SystemExit) as caught:
        entrypoint.main()

    assert caught.value.code == 2


def test_non_claim_command_preserves_existing_cli_path(monkeypatch):
    monkeypatch.setattr(
        entrypoint,
        "preflight_bounty",
        lambda *a: pytest.fail("non-claim command must not preflight"),
    )
    calls = []
    monkeypatch.setattr(entrypoint, "_cli_main", lambda: calls.append("cli"))
    monkeypatch.setattr(entrypoint.sys, "argv", ["concierge", "browse"])

    entrypoint.main()

    assert calls == ["cli"]
