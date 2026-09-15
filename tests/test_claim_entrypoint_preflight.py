# SPDX-License-Identifier: MIT
"""Installed claim composes payoff, preflight, and availability authority."""

import json

import pytest

from concierge import entrypoint as e

BUNDLE = "/tmp/payoff-bundle"


def _result(disposition="ACTIONABLE", codes=(), attempts=1, prs=1):
    return {
        "attempt_count": attempts,
        "canonical_audit": {"open_pr_count": prs, "comments": ["DO NOT ECHO"]},
        "qualification": {
            "disposition": disposition,
            "dispatch": disposition == "ACTIONABLE",
            "reason_codes": list(codes),
            "reasons": [
                {"code": code, "severity": disposition, "message": "safe"}
                for code in codes
            ],
            "signals": {},
        },
    }


def _availability(dispatch=True, reason=None, signals=()):
    return {
        "dispatch": dispatch,
        "disposition": "CLEAR" if dispatch else "HOLD",
        "reason_code": reason,
        "signal_codes": list(signals),
        "evidence": [{"body": "DO NOT ECHO", "user": "secret"}],
    }


def _argv(*, repo="acme/widget", json_out=False, dry=False):
    out = ["concierge"] + (["--json"] if json_out else []) + [
        "claim",
        "--repo",
        repo,
        "--issue",
        "42",
        "--wallet",
        "alice",
        "--payoff-bundle",
        BUNDLE,
    ]
    return out + (["--dry-run"] if dry else [])


def _allow(monkeypatch, seen=None):
    def payoff(repo, issue, bundle):
        if seen is not None:
            seen.append(("payoff", repo, issue, bundle))
        return {}

    monkeypatch.setattr(e, "verify_claim_payoff_bundle", payoff)
    monkeypatch.setattr(
        e,
        "preflight_bounty",
        lambda repo, issue: (
            seen.append(("preflight", repo, issue)) if seen is not None else None
        )
        or _result(),
    )
    monkeypatch.setattr(
        e,
        "inspect_bounty_availability",
        lambda repo, issue: (
            seen.append(("availability", repo, issue)) if seen is not None else None
        )
        or _availability(),
    )


def test_live_claim_orders_authorities_strips_option_and_restores_argv(monkeypatch):
    seen = []
    _allow(monkeypatch, seen)

    def cli():
        seen.append(("cli", list(e.sys.argv)))
        assert "--payoff-bundle" not in e.sys.argv
        assert BUNDLE not in e.sys.argv

    monkeypatch.setattr(e, "_cli_main", cli)
    original = _argv()
    monkeypatch.setattr(e.sys, "argv", original)

    e.main()

    assert seen[:3] == [
        ("payoff", "acme/widget", 42, BUNDLE),
        ("preflight", "acme/widget", 42),
        ("availability", "acme/widget", 42),
    ]
    assert seen[3][0] == "cli"
    assert e.sys.argv is original


def test_short_repo_and_equals_forms_are_normalized(monkeypatch):
    seen = []
    _allow(monkeypatch, seen)
    monkeypatch.setattr(e, "_cli_main", lambda: None)
    monkeypatch.setattr(
        e.sys,
        "argv",
        [
            "concierge",
            "claim",
            "--repo",
            "widget",
            "--issue=42",
            "--wallet",
            "alice",
            f"--payoff-bundle={BUNDLE}",
        ],
    )

    e.main()

    assert seen == [
        ("payoff", "Scottcjn/widget", 42, BUNDLE),
        ("preflight", "Scottcjn/widget", 42),
        ("availability", "Scottcjn/widget", 42),
    ]


@pytest.mark.parametrize(
    "args,code",
    [
        (
            ["concierge", "claim", "--issue", "42", "--wallet", "alice"],
            "BUNDLE_REQUIRED",
        ),
        (
            [
                "concierge",
                "claim",
                "--issue",
                "42",
                "--payoff-bundle",
                "--wallet",
                "alice",
            ],
            "BUNDLE_REQUIRED",
        ),
        (
            [
                "concierge",
                "claim",
                "--issue",
                "42",
                "--wallet",
                "alice",
                "--payoff-bundle=",
            ],
            "BUNDLE_REQUIRED",
        ),
        (
            _argv() + ["--payoff-bundle", "/tmp/other"],
            "DUPLICATE_BUNDLE_OPTION",
        ),
    ],
)
def test_missing_malformed_or_duplicate_bundle_fails_before_authority(
    monkeypatch, capsys, args, code
):
    for name in ("verify_claim_payoff_bundle", "preflight_bounty", "_cli_main"):
        monkeypatch.setattr(e, name, lambda *x, n=name: pytest.fail(n))
    monkeypatch.setattr(e.sys, "argv", args)

    with pytest.raises(SystemExit) as caught:
        e.main()

    assert caught.value.code == 2
    assert code in capsys.readouterr().err


def test_payoff_failure_is_safe_json_and_short_circuits_network(monkeypatch, capsys):
    def fail(*_):
        raise e.ClaimPayoffError("CLAIM_TARGET_NOT_IN_BUNDLE", "DO NOT ECHO")

    monkeypatch.setattr(e, "verify_claim_payoff_bundle", fail)
    monkeypatch.setattr(e, "preflight_bounty", lambda *_: pytest.fail("network"))
    monkeypatch.setattr(
        e, "inspect_bounty_availability", lambda *_: pytest.fail("network")
    )
    monkeypatch.setattr(e, "_cli_main", lambda: pytest.fail("cli"))
    monkeypatch.setattr(e.sys, "argv", _argv(json_out=True))

    with pytest.raises(SystemExit) as caught:
        e.main()

    output = capsys.readouterr().out
    assert caught.value.code == 2
    assert json.loads(output) == {
        "error": "claim_payoff_unavailable",
        "reason_code": "CLAIM_TARGET_NOT_IN_BUNDLE",
    }
    assert "DO NOT ECHO" not in output


@pytest.mark.parametrize(
    "disposition,code,exit_code",
    [
        ("HOLD", "SATURATED", 2),
        ("REJECT", "ALREADY_REWARDED", 3),
    ],
)
def test_preflight_block_is_safe_and_short_circuits(
    monkeypatch, capsys, disposition, code, exit_code
):
    _allow(monkeypatch)
    monkeypatch.setattr(
        e,
        "preflight_bounty",
        lambda *_: _result(disposition, [code], 5, 2),
    )
    monkeypatch.setattr(
        e,
        "inspect_bounty_availability",
        lambda *_: pytest.fail("availability"),
    )
    monkeypatch.setattr(e, "_cli_main", lambda: pytest.fail("cli"))
    monkeypatch.setattr(e.sys, "argv", _argv(json_out=True))

    with pytest.raises(SystemExit) as caught:
        e.main()

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert caught.value.code == exit_code
    assert payload["qualification"]["reason_codes"] == [code]
    assert payload["attempt_count"] == 5
    assert "DO NOT ECHO" not in output


def test_terminal_availability_blocks_without_exposing_source(monkeypatch, capsys):
    _allow(monkeypatch)
    monkeypatch.setattr(
        e,
        "inspect_bounty_availability",
        lambda *_: _availability(False, "TERMINAL", ["ACCEPTED"]),
    )
    monkeypatch.setattr(e, "_cli_main", lambda: pytest.fail("cli"))
    monkeypatch.setattr(e.sys, "argv", _argv(json_out=True))

    with pytest.raises(SystemExit) as caught:
        e.main()

    output = capsys.readouterr().out
    qualification = json.loads(output)["qualification"]
    assert caught.value.code == 2
    assert qualification["reason_codes"] == ["AVAILABILITY:TERMINAL"]
    assert qualification["signals"]["availability_signal_codes"] == ["ACCEPTED"]
    assert "DO NOT ECHO" not in output
    assert "secret" not in output


def test_dry_run_help_and_non_claim_skip_new_authorities(monkeypatch):
    for name in (
        "verify_claim_payoff_bundle",
        "preflight_bounty",
        "inspect_bounty_availability",
    ):
        monkeypatch.setattr(e, name, lambda *x, n=name: pytest.fail(n))
    calls = []
    monkeypatch.setattr(e, "_cli_main", lambda: calls.append(list(e.sys.argv)))

    for args in (
        _argv(dry=True),
        ["concierge", "claim", "--help", "--payoff-bundle", BUNDLE],
        ["concierge", "claim", "--help", "--payoff-bundle", "--wallet", "alice"],
        ["concierge", "browse"],
    ):
        monkeypatch.setattr(e.sys, "argv", args)
        e.main()

    assert calls[0][-1] == "--dry-run"
    assert all("--payoff-bundle" not in call for call in calls)
    # A malformed preview flag must not swallow the next real option.
    assert "--wallet" in calls[2]


@pytest.mark.parametrize(
    "authority,exc",
    [
        ("preflight_bounty", e.BountyPreflightError("down")),
        ("inspect_bounty_availability", e.BountyAvailabilityError("down")),
    ],
)
def test_provider_failures_fail_closed(monkeypatch, capsys, authority, exc):
    _allow(monkeypatch)
    monkeypatch.setattr(e, authority, lambda *_: (_ for _ in ()).throw(exc))
    monkeypatch.setattr(e, "_cli_main", lambda: pytest.fail("cli"))
    monkeypatch.setattr(e.sys, "argv", _argv())

    with pytest.raises(SystemExit) as caught:
        e.main()

    assert caught.value.code == 2
    assert "claim preflight unavailable" in capsys.readouterr().err


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"dispatch": 1, "disposition": "CLEAR"},
        {"dispatch": True, "disposition": "HOLD"},
        {
            "dispatch": False,
            "disposition": "CLEAR",
            "reason_code": "X",
            "signal_codes": [],
        },
        {
            "dispatch": False,
            "disposition": "HOLD",
            "reason_code": None,
            "signal_codes": [],
        },
        {
            "dispatch": False,
            "disposition": "HOLD",
            "reason_code": "X",
            "signal_codes": [1],
        },
    ],
)
def test_malformed_availability_fails_closed(monkeypatch, bad):
    _allow(monkeypatch)
    monkeypatch.setattr(e, "inspect_bounty_availability", lambda *_: bad)
    monkeypatch.setattr(e, "_cli_main", lambda: pytest.fail("cli"))
    monkeypatch.setattr(e.sys, "argv", _argv())

    with pytest.raises(SystemExit) as caught:
        e.main()

    assert caught.value.code == 2


def test_malformed_preflight_fails_closed(monkeypatch):
    _allow(monkeypatch)
    monkeypatch.setattr(e, "preflight_bounty", lambda *_: {"qualification": {}})
    monkeypatch.setattr(
        e,
        "inspect_bounty_availability",
        lambda *_: pytest.fail("availability"),
    )
    monkeypatch.setattr(e, "_cli_main", lambda: pytest.fail("cli"))
    monkeypatch.setattr(e.sys, "argv", _argv())

    with pytest.raises(SystemExit) as caught:
        e.main()

    assert caught.value.code == 2
