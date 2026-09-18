# SPDX-License-Identifier: MIT
"""Installed claim composes payoff, economics, preflight, and availability."""

import json

import pytest

from concierge import entrypoint as e

BUNDLE = "/tmp/payoff-bundle"
ECONOMIC_RECEIPT = "/tmp/economic-receipt.json"
ECONOMIC_RECEIPT_SHA = "a" * 64
ECONOMIC_POLICY_SHA = "b" * 64
ECONOMIC_AS_OF = "2026-09-17T20:30:00Z"


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
        "--economic-receipt",
        ECONOMIC_RECEIPT,
        "--economic-receipt-sha256",
        ECONOMIC_RECEIPT_SHA,
        "--economic-policy-sha256",
        ECONOMIC_POLICY_SHA,
        "--economic-as-of",
        ECONOMIC_AS_OF,
    ]
    return out + (["--dry-run"] if dry else [])


def _allow(monkeypatch, seen=None):
    def payoff(repo, issue, bundle):
        if seen is not None:
            seen.append(("payoff", repo, issue, bundle))
        return {
            "schema": "payoff-claim-proof/v2",
            "repo": repo,
            "issue": issue,
            "canonical_issue_url": f"https://github.com/{repo}/issues/{issue}",
            "work_id": "work-42",
        }

    def economics(
        repo,
        issue,
        payoff_proof,
        receipt_path,
        *,
        expected_receipt_bytes_sha256,
        expected_policy_sha256,
        decision_as_of,
    ):
        if seen is not None:
            seen.append(
                (
                    "economics",
                    repo,
                    issue,
                    payoff_proof["work_id"],
                    receipt_path,
                    expected_receipt_bytes_sha256,
                    expected_policy_sha256,
                    decision_as_of,
                )
            )
        return {"schema": "claim-economic-admission-proof/v1", "verified": True}

    monkeypatch.setattr(e, "verify_claim_payoff_bundle", payoff)
    monkeypatch.setattr(e, "verify_claim_economic_receipt", economics)
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
        for wrapper_option in (
            "--payoff-bundle",
            "--economic-receipt",
            "--economic-receipt-sha256",
            "--economic-policy-sha256",
            "--economic-as-of",
        ):
            assert wrapper_option not in e.sys.argv
        assert BUNDLE not in e.sys.argv
        assert ECONOMIC_RECEIPT not in e.sys.argv

    monkeypatch.setattr(e, "_cli_main", cli)
    original = _argv()
    monkeypatch.setattr(e.sys, "argv", original)

    e.main()

    assert [item[0] for item in seen[:4]] == [
        "payoff",
        "economics",
        "preflight",
        "availability",
    ]
    assert seen[0] == ("payoff", "acme/widget", 42, BUNDLE)
    assert seen[1] == (
        "economics",
        "acme/widget",
        42,
        "work-42",
        ECONOMIC_RECEIPT,
        ECONOMIC_RECEIPT_SHA,
        ECONOMIC_POLICY_SHA,
        ECONOMIC_AS_OF,
    )
    assert seen[4][0] == "cli"
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

    assert [item[0] for item in seen] == [
        "payoff",
        "economics",
        "preflight",
        "availability",
    ]
    assert seen[0] == ("payoff", "Scottcjn/widget", 42, BUNDLE)
    assert seen[1][1:4] == ("Scottcjn/widget", 42, "work-42")


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
    for name in (
        "verify_claim_payoff_bundle",
        "verify_claim_economic_receipt",
        "preflight_bounty",
        "_cli_main",
    ):
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
        "verify_claim_economic_receipt",
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

def test_missing_economic_option_fails_after_payoff_before_provider(monkeypatch, capsys):
    seen = []

    def payoff(repo, issue, bundle):
        seen.append(("payoff", repo, issue, bundle))
        return {
            "schema": "payoff-claim-proof/v2",
            "repo": repo,
            "issue": issue,
            "canonical_issue_url": f"https://github.com/{repo}/issues/{issue}",
            "work_id": "work-42",
        }

    monkeypatch.setattr(e, "verify_claim_payoff_bundle", payoff)
    monkeypatch.setattr(
        e, "verify_claim_economic_receipt", lambda *_a, **_k: pytest.fail("economic")
    )
    monkeypatch.setattr(e, "preflight_bounty", lambda *_: pytest.fail("provider"))
    monkeypatch.setattr(e, "_cli_main", lambda: pytest.fail("cli"))
    args = _argv()
    index = args.index("--economic-receipt")
    del args[index : index + 2]
    monkeypatch.setattr(e.sys, "argv", args)

    with pytest.raises(SystemExit) as caught:
        e.main()

    assert caught.value.code == 2
    assert seen == [("payoff", "acme/widget", 42, BUNDLE)]
    assert "ECONOMIC_RECEIPT_REQUIRED" in capsys.readouterr().err


def test_duplicate_economic_option_fails_before_provider(monkeypatch, capsys):
    _allow(monkeypatch)
    monkeypatch.setattr(e, "preflight_bounty", lambda *_: pytest.fail("provider"))
    monkeypatch.setattr(e, "_cli_main", lambda: pytest.fail("cli"))
    monkeypatch.setattr(
        e.sys,
        "argv",
        _argv() + ["--economic-as-of", ECONOMIC_AS_OF],
    )

    with pytest.raises(SystemExit) as caught:
        e.main()

    assert caught.value.code == 2
    assert "DUPLICATE_ECONOMIC_OPTION" in capsys.readouterr().err


def test_economic_hold_is_safe_json_and_short_circuits_provider(monkeypatch, capsys):
    _allow(monkeypatch)

    def fail(*_a, **_k):
        raise e.ClaimEconomicAdmissionError(
            "ECONOMICS_HOLD_VALUE_UNKNOWN",
            "hostile receipt text DO NOT ECHO",
        )

    monkeypatch.setattr(e, "verify_claim_economic_receipt", fail)
    monkeypatch.setattr(e, "preflight_bounty", lambda *_: pytest.fail("provider"))
    monkeypatch.setattr(
        e, "inspect_bounty_availability", lambda *_: pytest.fail("provider")
    )
    monkeypatch.setattr(e, "_cli_main", lambda: pytest.fail("cli"))
    monkeypatch.setattr(e.sys, "argv", _argv(json_out=True))

    with pytest.raises(SystemExit) as caught:
        e.main()

    output = capsys.readouterr().out
    assert caught.value.code == 2
    assert json.loads(output) == {
        "error": "claim_economics_unavailable",
        "reason_code": "ECONOMICS_HOLD_VALUE_UNKNOWN",
    }
    assert "DO NOT ECHO" not in output

