# SPDX-License-Identifier: MIT
"""Installed claim composes payoff, economics, preflight, availability, and live cash."""

from datetime import datetime, timezone
import json

import pytest

from concierge import entrypoint as e

BUNDLE = "/tmp/payoff-bundle"
ECONOMIC_REQUEST = "/tmp/economic-request.json"
ECONOMIC_RECEIPT = "/tmp/economic-receipt.json"


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


def _cash(
    disposition="ACTIVE_REVIEW",
    *,
    route="main_bounty_queue",
    reasons=(),
    amount="50",
):
    return {
        "schema": "bounty-live-cash-admission-receipt/v1",
        "economics": {
            "currency": "USD" if amount is not None else None,
            "fixed_amount": amount,
            "fixed_semantics": amount is not None,
            "active_floor": "50",
            "pile_floor": "10",
        },
        "disposition": disposition,
        "route": route,
        "reason_codes": list(reasons),
        "authority": {
            "advisory_only": True,
            "claim_authority": False,
            "implementation_authority": False,
            "submission_authority": False,
            "outbound_contact_authority": False,
            "payment_or_wallet_authority": False,
        },
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
        "--economic-request",
        ECONOMIC_REQUEST,
        "--economic-receipt",
        ECONOMIC_RECEIPT,
    ]
    return out + (["--dry-run"] if dry else [])


def _override_economic_verifier(monkeypatch, verifier, cash=None):
    if cash is None:
        cash = lambda *_args, **_kwargs: _cash()
    monkeypatch.setattr(e, "verify_claim_economic_receipt", verifier)
    monkeypatch.setattr(e, "evaluate_live_cash_admission", cash)
    monkeypatch.setattr(
        e,
        "_preflight_claim",
        e._build_preflight_claim(
            verifier,
            cash,
            now=datetime.now,
            utc=timezone.utc,
        ),
    )


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
        request_path,
        receipt_path,
        *,
        decision_as_of,
    ):
        if seen is not None:
            seen.append(
                (
                    "economics",
                    repo,
                    issue,
                    payoff_proof["work_id"],
                    request_path,
                    receipt_path,
                    decision_as_of,
                )
            )
        return {"schema": "claim-economic-admission-proof/v2", "verified": True}

    def cash(repo, issue):
        if seen is not None:
            seen.append(("live_cash", repo, issue))
        return _cash()

    monkeypatch.setattr(e, "verify_claim_payoff_bundle", payoff)
    _override_economic_verifier(monkeypatch, economics, cash)
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
            "--economic-request",
            "--economic-receipt",
        ):
            assert wrapper_option not in e.sys.argv
        assert BUNDLE not in e.sys.argv
        assert ECONOMIC_REQUEST not in e.sys.argv
        assert ECONOMIC_RECEIPT not in e.sys.argv

    monkeypatch.setattr(e, "_cli_main", cli)
    original = _argv()
    monkeypatch.setattr(e.sys, "argv", original)

    e.main()

    assert [item[0] for item in seen[:5]] == [
        "payoff",
        "economics",
        "preflight",
        "availability",
        "live_cash",
    ]
    assert seen[0] == ("payoff", "acme/widget", 42, BUNDLE)
    assert seen[1][:6] == (
        "economics",
        "acme/widget",
        42,
        "work-42",
        ECONOMIC_REQUEST,
        ECONOMIC_RECEIPT,
    )
    assert seen[1][6].endswith("Z")
    assert seen[5][0] == "cli"
    assert e.sys.argv is original


def test_live_preflight_has_no_clock_defaults():
    assert e._preflight_claim.__defaults__ is None
    assert e._preflight_claim.__kwdefaults__ is None


def test_live_preflight_uses_process_owned_utc_clock(monkeypatch):
    seen = []
    _allow(monkeypatch, seen)
    trusted_now = datetime(2026, 9, 17, 20, 30, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        e,
        "_preflight_claim",
        e._build_preflight_claim(
            e.verify_claim_economic_receipt,
            e.evaluate_live_cash_admission,
            now=lambda tz: trusted_now.astimezone(tz),
            utc=timezone.utc,
        ),
    )
    e._preflight_claim(_argv())
    assert seen[1][:6] == (
        "economics",
        "acme/widget",
        42,
        "work-42",
        ECONOMIC_REQUEST,
        ECONOMIC_RECEIPT,
    )
    assert seen[1][6] == "2026-09-17T20:30:00Z"


def test_live_cash_evaluator_is_generation_bound_after_builder(monkeypatch):
    seen = []
    _allow(monkeypatch, seen)
    bound = e._preflight_claim
    monkeypatch.setattr(
        e,
        "evaluate_live_cash_admission",
        lambda *_args, **_kwargs: pytest.fail("rebound live cash evaluator"),
    )
    bound(_argv())
    assert [item[0] for item in seen][-2:] == ["availability", "live_cash"]


@pytest.mark.parametrize(
    ("disposition", "route", "amount", "reason"),
    [
        ("PILE_SAVE_UP", "bounty_pile_10_49", "20", "LIVE_CASH:PILE_SAVE_UP"),
        ("PRUNE_BELOW_DOLLAR_FLOOR", None, "5", "LIVE_CASH:PRUNE_BELOW_DOLLAR_FLOOR"),
        ("HOLD_NO_FIXED_USD_REWARD", None, None, "LIVE_CASH:HOLD_NO_FIXED_USD_REWARD"),
    ],
)
def test_live_cash_nonactive_routes_block_claim_instructions(
    monkeypatch, capsys, disposition, route, amount, reason
):
    seen = []
    _allow(monkeypatch, seen)

    def cash(repo, issue):
        seen.append(("live_cash", repo, issue))
        return _cash(disposition, route=route, amount=amount)

    _override_economic_verifier(monkeypatch, e.verify_claim_economic_receipt, cash)
    monkeypatch.setattr(e, "_cli_main", lambda: pytest.fail("cli"))
    monkeypatch.setattr(e.sys, "argv", _argv(json_out=True))

    with pytest.raises(SystemExit) as caught:
        e.main()

    assert caught.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["qualification"]["reason_codes"][0] == reason
    assert [item[0] for item in seen][-3:] == [
        "preflight",
        "availability",
        "live_cash",
    ]


def test_live_cash_malformed_authority_fails_closed(monkeypatch, capsys):
    _allow(monkeypatch)

    def cash(*_args, **_kwargs):
        receipt = _cash()
        receipt["authority"]["claim_authority"] = True
        return receipt

    _override_economic_verifier(monkeypatch, e.verify_claim_economic_receipt, cash)
    monkeypatch.setattr(e, "_cli_main", lambda: pytest.fail("cli"))
    monkeypatch.setattr(e.sys, "argv", _argv(json_out=True))

    with pytest.raises(SystemExit) as caught:
        e.main()

    assert caught.value.code == 2
    assert json.loads(capsys.readouterr().out) == {
        "error": "claim_preflight_unavailable"
    }


def test_live_preflight_ignores_public_economic_verifier_rebinding(monkeypatch, tmp_path):
    original = e._preflight_claim
    monkeypatch.setattr(
        e,
        "verify_claim_economic_receipt",
        lambda *_args, **_kwargs: {"verified": True},
    )
    monkeypatch.setattr(
        e,
        "verify_claim_payoff_bundle",
        lambda repo, issue, _bundle: {
            "schema": "payoff-claim-proof/v2",
            "repo": repo,
            "issue": issue,
            "canonical_issue_url": f"https://github.com/{repo}/issues/{issue}",
            "work_id": "work-42",
        },
    )
    monkeypatch.setattr(e, "preflight_bounty", lambda *_: pytest.fail("provider"))
    monkeypatch.setattr(
        e,
        "inspect_bounty_availability",
        lambda *_: pytest.fail("provider"),
    )

    args = _argv()
    args[args.index(ECONOMIC_REQUEST)] = str(tmp_path / "missing-request.json")
    args[args.index(ECONOMIC_RECEIPT)] = str(tmp_path / "missing-receipt.json")
    with pytest.raises(e.ClaimEconomicAdmissionError) as caught:
        e._preflight_claim(args)

    assert caught.value.code == "INVALID_ECONOMIC_RECEIPT"
    assert e._preflight_claim is original


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
            f"--economic-request={ECONOMIC_REQUEST}",
            f"--economic-receipt={ECONOMIC_RECEIPT}",
        ],
    )

    e.main()

    assert [item[0] for item in seen] == [
        "payoff",
        "economics",
        "preflight",
        "availability",
        "live_cash",
    ]
    assert seen[0] == ("payoff", "Scottcjn/widget", 42, BUNDLE)
    assert seen[1][1:6] == (
        "Scottcjn/widget", 42, "work-42", ECONOMIC_REQUEST, ECONOMIC_RECEIPT
    )


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
        "evaluate_live_cash_admission",
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
    for wrapper_option in (
        "--payoff-bundle",
        "--economic-request",
        "--economic-receipt",
    ):
        assert all(wrapper_option not in call for call in calls)
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
        _argv() + ["--economic-receipt", ECONOMIC_RECEIPT],
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

    _override_economic_verifier(monkeypatch, fail)
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

