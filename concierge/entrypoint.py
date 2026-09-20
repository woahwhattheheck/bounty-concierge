# SPDX-License-Identifier: MIT
"""Console entry point with sanitized domain-error handling."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sys
from typing import Any

from concierge.bounty_audit import BountyAuditError
from concierge.bounty_availability import (
    BountyAvailabilityError,
    inspect_bounty_availability,
)
from concierge.bounty_live_cash_admission import (
    LiveCashAdmissionError,
    evaluate_live_cash_admission,
)
from concierge.bounty_preflight import BountyPreflightError, preflight_bounty
from concierge.bounty_qualification import QualificationInputError
from concierge.claim_economic_admission import (
    ClaimEconomicAdmissionError,
    verify_claim_economic_receipt,
)
from concierge.cli import main as _cli_main
from concierge.payoff_claim_gate import ClaimPayoffError, verify_claim_payoff_bundle
from concierge.payout_tracker import PayoutLookupError


class ClaimPreflightBlocked(RuntimeError):
    """Safe, source-text-free blocked-claim result for the console boundary."""

    def __init__(
        self,
        *,
        repo: str,
        issue: int,
        qualification: dict[str, Any],
        attempt_count: int | None,
        open_pr_count: int | None,
    ) -> None:
        self.repo = repo
        self.issue = issue
        self.qualification = qualification
        self.attempt_count = attempt_count
        self.open_pr_count = open_pr_count
        self.exit_code = 3 if qualification.get("disposition") == "REJECT" else 2
        codes = ",".join(qualification.get("reason_codes", [])) or "none"
        super().__init__(
            f"{repo}#{issue} disposition={qualification.get('disposition')} "
            f"attempts={attempt_count if attempt_count is not None else 'unknown'} "
            f"open_prs={open_pr_count if open_pr_count is not None else 'unknown'} "
            f"reasons={codes}"
        )


def _json_requested() -> bool:
    return "--json" in sys.argv[1:]


def _option_value(args: list[str], name: str) -> str | None:
    for index, arg in enumerate(args):
        if arg == name:
            return args[index + 1] if index + 1 < len(args) else None
        prefix = f"{name}="
        if arg.startswith(prefix):
            return arg[len(prefix) :]
    return None


def _option_values(args: list[str], name: str) -> list[str | None]:
    """Collect wrapper option values without consuming another option as a value."""
    values: list[str | None] = []
    prefix = f"{name}="
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == name:
            if index + 1 < len(args) and not args[index + 1].startswith("-"):
                values.append(args[index + 1])
                index += 2
            else:
                values.append(None)
                index += 1
            continue
        if arg.startswith(prefix):
            values.append(arg[len(prefix) :])
        index += 1
    return values


def _claim_tail(argv: list[str]) -> list[str] | None:
    try:
        command_index = argv.index("claim")
    except ValueError:
        return None
    return argv[command_index + 1 :]


def _claim_target(argv: list[str]) -> tuple[str, int] | None:
    """Extract a valid live claim target; leave syntax errors to the main CLI."""
    tail = _claim_tail(argv)
    if tail is None:
        return None
    if "--dry-run" in argv or "-h" in tail or "--help" in tail:
        return None
    issue_text = _option_value(tail, "--issue")
    if issue_text is None:
        return None
    try:
        issue = int(issue_text)
    except ValueError:
        return None
    if issue <= 0:
        return None
    repo = _option_value(tail, "--repo") or "Scottcjn/rustchain-bounties"
    if "/" not in repo:
        repo = f"Scottcjn/{repo}"
    return repo, issue


def _claim_payoff_bundle(argv: list[str]) -> str:
    """Require exactly one entrypoint-owned payoff bundle option for live claims."""
    tail = _claim_tail(argv)
    if tail is None:
        raise ClaimPayoffError("BUNDLE_REQUIRED", "live claim requires --payoff-bundle")
    values = _option_values(tail, "--payoff-bundle")
    if not values or values[0] is None or values[0] == "":
        raise ClaimPayoffError(
            "BUNDLE_REQUIRED",
            "live claim requires --payoff-bundle DIR",
        )
    if len(values) != 1:
        raise ClaimPayoffError(
            "DUPLICATE_BUNDLE_OPTION",
            "live claim accepts exactly one --payoff-bundle option",
        )
    return values[0]


def _claim_economic_options(argv: list[str]) -> dict[str, str]:
    """Require retained request + receipt evidence for live claims."""

    tail = _claim_tail(argv)
    if tail is None:
        raise ClaimEconomicAdmissionError(
            "ECONOMIC_RECEIPT_REQUIRED",
            "live claim requires economic admission evidence",
        )
    specs = (
        (
            "--economic-request",
            "ECONOMIC_REQUEST_REQUIRED",
            "live claim requires --economic-request FILE",
        ),
        (
            "--economic-receipt",
            "ECONOMIC_RECEIPT_REQUIRED",
            "live claim requires --economic-receipt FILE",
        ),
    )
    result: dict[str, str] = {}
    for option, missing_code, message in specs:
        values = _option_values(tail, option)
        if not values or values[0] is None or values[0] == "":
            raise ClaimEconomicAdmissionError(missing_code, message)
        if len(values) != 1:
            raise ClaimEconomicAdmissionError(
                "DUPLICATE_ECONOMIC_OPTION",
                f"live claim accepts exactly one {option} option",
            )
        result[option] = values[0]
    return result

def _strip_claim_wrapper_options(argv: list[str]) -> list[str]:
    """Remove entrypoint-owned claim options before handing off to argparse."""

    try:
        command_index = argv.index("claim")
    except ValueError:
        return list(argv)

    owned = {
        "--payoff-bundle",
        "--economic-request",
        "--economic-receipt",
    }
    result = list(argv[: command_index + 1])
    tail = argv[command_index + 1 :]
    index = 0
    while index < len(tail):
        arg = tail[index]
        matched = next(
            (
                option
                for option in owned
                if arg == option or arg.startswith(f"{option}=")
            ),
            None,
        )
        if matched is None:
            result.append(arg)
            index += 1
            continue
        if arg == matched:
            index += 1
            if index < len(tail) and not tail[index].startswith("-"):
                index += 1
        else:
            index += 1
    return result


def _claim_counts(result: dict[str, Any]) -> tuple[int | None, int | None]:
    """Extract only safe occupancy counts from canonical preflight."""
    audit = result.get("canonical_audit")
    if not isinstance(audit, dict):
        audit = {}
    attempt_count = result.get("attempt_count")
    if isinstance(attempt_count, bool) or not isinstance(attempt_count, int):
        attempt_count = None
    open_pr_count = audit.get("open_pr_count")
    if isinstance(open_pr_count, bool) or not isinstance(open_pr_count, int):
        open_pr_count = None
    return attempt_count, open_pr_count



_LIVE_CASH_AUTHORITY = {
    "advisory_only": True,
    "claim_authority": False,
    "implementation_authority": False,
    "submission_authority": False,
    "outbound_contact_authority": False,
    "payment_or_wallet_authority": False,
}
_LIVE_CASH_DISPOSITIONS = {
    "ACTIVE_REVIEW",
    "PILE_SAVE_UP",
    "PRUNE_BELOW_DOLLAR_FLOOR",
    "HOLD_SOURCE_GENERATION_CHANGED",
    "REJECT_CANONICAL_PREFLIGHT",
    "HOLD_CANONICAL_PREFLIGHT",
    "HOLD_NON_FIXED_USD_REWARD",
    "HOLD_MIXED_REWARD_CURRENCY",
    "HOLD_NO_FIXED_USD_REWARD",
}


def _live_cash_block(
    receipt: dict[str, Any],
    *,
    repo: str,
    issue: int,
) -> tuple[dict[str, Any] | None, int | None, int | None]:
    """Reduce source-bound 10/50 cash admission to safe claim authority."""
    if not isinstance(receipt, dict) or receipt.get("schema") != "bounty-live-cash-admission-receipt/v1":
        raise LiveCashAdmissionError("live cash admission returned malformed schema")
    identity = receipt.get("identity")
    if identity != {
        "repo": repo,
        "issue_number": issue,
        "canonical_issue_url": f"https://github.com/{repo}/issues/{issue}",
    }:
        raise LiveCashAdmissionError("live cash admission target identity mismatch")
    if receipt.get("authority") != _LIVE_CASH_AUTHORITY:
        raise LiveCashAdmissionError("live cash admission authority mismatch")

    disposition = receipt.get("disposition")
    reason_codes = receipt.get("reason_codes")
    if disposition not in _LIVE_CASH_DISPOSITIONS:
        raise LiveCashAdmissionError("live cash admission disposition is unsupported")
    if not isinstance(reason_codes, list) or not all(
        isinstance(code, str) and code for code in reason_codes
    ):
        raise LiveCashAdmissionError("live cash admission reason codes are malformed")

    source = receipt.get("source")
    projection = source.get("preflight") if isinstance(source, dict) else None
    if not isinstance(projection, dict):
        raise LiveCashAdmissionError("live cash admission preflight projection is malformed")
    signals = projection.get("signals")
    if not isinstance(signals, dict):
        raise LiveCashAdmissionError("live cash admission preflight signals are malformed")

    attempt_count = signals.get("attempt_count")
    if isinstance(attempt_count, bool) or not isinstance(attempt_count, int):
        attempt_count = None
    open_pr_count = signals.get("open_pr_count")
    if isinstance(open_pr_count, bool) or not isinstance(open_pr_count, int):
        open_pr_count = None

    route = receipt.get("route")
    economics = receipt.get("economics")
    if not isinstance(economics, dict):
        raise LiveCashAdmissionError("live cash admission economics are malformed")
    if economics.get("active_floor") != "50" or economics.get("pile_floor") != "10":
        raise LiveCashAdmissionError("live cash admission dollar-floor generation mismatch")

    expected_route = (
        "main_bounty_queue"
        if disposition == "ACTIVE_REVIEW"
        else "bounty_pile_10_49"
        if disposition == "PILE_SAVE_UP"
        else None
    )
    if route != expected_route:
        raise LiveCashAdmissionError("live cash admission route mismatch")

    if disposition == "ACTIVE_REVIEW":
        if (
            projection.get("dispatch") is not True
            or economics.get("currency") != "USD"
            or economics.get("fixed_semantics") is not True
            or not isinstance(economics.get("fixed_amount"), str)
            or not economics["fixed_amount"]
        ):
            raise LiveCashAdmissionError("active live cash admission is malformed")
        return None, attempt_count, open_pr_count

    if disposition in {"REJECT_CANONICAL_PREFLIGHT", "HOLD_CANONICAL_PREFLIGHT"}:
        canonical_disposition = projection.get("disposition")
        canonical_codes = projection.get("reason_codes")
        if (
            canonical_disposition not in {"REJECT", "HOLD"}
            or projection.get("dispatch") is not False
            or not isinstance(canonical_codes, list)
            or not all(isinstance(code, str) and code for code in canonical_codes)
        ):
            raise LiveCashAdmissionError("blocked canonical preflight projection is malformed")
        return (
            {
                "disposition": canonical_disposition,
                "dispatch": False,
                "reason_codes": list(canonical_codes),
                "reasons": [
                    {
                        "code": code,
                        "severity": canonical_disposition,
                        "message": "Canonical live source evidence blocks claim dispatch.",
                    }
                    for code in canonical_codes
                ],
                "signals": {},
            },
            attempt_count,
            open_pr_count,
        )

    qualified = [f"LIVE_CASH:{disposition}"] + [
        f"LIVE_CASH:{code}" for code in reason_codes
    ]
    return (
        {
            "disposition": "HOLD",
            "dispatch": False,
            "reason_codes": qualified,
            "reasons": [
                {
                    "code": code,
                    "severity": "HOLD",
                    "message": "Source-bound live cash admission blocks claim dispatch.",
                }
                for code in qualified
            ],
            "signals": {"live_cash_disposition": disposition},
        },
        attempt_count,
        open_pr_count,
    )


def _availability_block(
    availability: dict[str, Any],
) -> dict[str, Any] | None:
    """Reduce availability authority to a safe synthetic HOLD qualification."""
    if not isinstance(availability, dict):
        raise BountyAvailabilityError(
            "canonical bounty availability did not return an object"
        )
    dispatch = availability.get("dispatch")
    disposition = availability.get("disposition")
    if type(dispatch) is not bool or not isinstance(disposition, str):
        raise BountyAvailabilityError(
            "canonical bounty availability returned malformed authority"
        )

    if dispatch:
        if disposition != "CLEAR":
            raise BountyAvailabilityError(
                "dispatchable bounty availability was not CLEAR"
            )
        return None

    if disposition != "HOLD":
        raise BountyAvailabilityError(
            "non-dispatchable bounty availability was not HOLD"
        )
    reason = availability.get("reason_code")
    signal_codes = availability.get("signal_codes")
    if not isinstance(reason, str) or not reason:
        raise BountyAvailabilityError(
            "blocked bounty availability was missing reason_code"
        )
    if not isinstance(signal_codes, list) or not all(
        isinstance(code, str) and code for code in signal_codes
    ):
        raise BountyAvailabilityError(
            "blocked bounty availability signal_codes were malformed"
        )

    qualified_reason = f"AVAILABILITY:{reason}"
    return {
        "disposition": "HOLD",
        "dispatch": False,
        "reason_codes": [qualified_reason],
        "reasons": [
            {
                "code": qualified_reason,
                "severity": "HOLD",
                "message": (
                    "Canonical maintainer availability evidence requires human "
                    "review before claim instructions are emitted."
                ),
            }
        ],
        "signals": {
            "availability_signal_codes": list(signal_codes),
        },
    }


def _build_preflight_claim(verify_economic, evaluate_cash, *, now, utc):
    """Bind verifier and time source into live preflight."""

    def _preflight_claim(argv: list[str]) -> None:
        """Require payoff, economics GO, canonical ACTIONABLE, and availability."""
        target = _claim_target(argv)
        if target is None:
            return
        repo, issue = target
    
        # Owner-supplied payoff evidence is deliberately first.  Do not spend provider
        # reads on a claim whose compensation path is absent, stale, exhausted,
        # ambiguous, or bound to a different target.
        payoff_proof = verify_claim_payoff_bundle(
            repo,
            issue,
            _claim_payoff_bundle(argv),
        )
    
        # Economic evidence is also local and deliberately precedes provider reads.
        # The verifier compiles no new valuation: it consumes the existing paid-work
        # gate receipt and requires an exact GO bound to this payoff-derived target.
        economic = _claim_economic_options(argv)
        decision_as_of = now(utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        verify_economic(
            repo,
            issue,
            payoff_proof,
            economic["--economic-request"],
            economic["--economic-receipt"],
            decision_as_of=decision_as_of,
        )

        # The source-bound 10/50 live-cash compiler is the front door for new
        # GitHub bounty work. Callers never supply reward amount or authority.
        cash_receipt = evaluate_cash(repo, issue)
        cash_block, cash_attempt_count, cash_open_pr_count = _live_cash_block(
            cash_receipt,
            repo=repo,
            issue=issue,
        )
        if cash_block is not None:
            raise ClaimPreflightBlocked(
                repo=repo,
                issue=issue,
                qualification=cash_block,
                attempt_count=cash_attempt_count,
                open_pr_count=cash_open_pr_count,
            )

        result = preflight_bounty(repo, issue)
        if not isinstance(result, dict):
            raise BountyPreflightError("canonical bounty preflight did not return an object")
        qualification = result.get("qualification")
        if not isinstance(qualification, dict) or not isinstance(
            qualification.get("dispatch"), bool
        ):
            raise BountyPreflightError(
                "canonical bounty preflight returned a malformed qualification"
            )
    
        attempt_count, open_pr_count = _claim_counts(result)
        if not qualification["dispatch"]:
            raise ClaimPreflightBlocked(
                repo=repo,
                issue=issue,
                qualification=qualification,
                attempt_count=attempt_count,
                open_pr_count=open_pr_count,
            )
    
        availability = inspect_bounty_availability(repo, issue)
        availability_block = _availability_block(availability)
        if availability_block is None:
            return
    
        raise ClaimPreflightBlocked(
            repo=repo,
            issue=issue,
            qualification=availability_block,
            attempt_count=attempt_count,
            open_pr_count=open_pr_count,
        )
    

    return _preflight_claim


_preflight_claim = _build_preflight_claim(
    verify_claim_economic_receipt,
    evaluate_live_cash_admission,
    now=datetime.now,
    utc=timezone.utc,
)

def _blocked_json(exc: ClaimPreflightBlocked) -> dict[str, Any]:
    """Return only safe preflight fields; never canonical comment bodies."""
    return {
        "error": "claim_blocked",
        "repo": exc.repo,
        "issue": exc.issue,
        "attempt_count": exc.attempt_count,
        "open_pr_count": exc.open_pr_count,
        "qualification": exc.qualification,
    }


def main() -> None:
    """Run the CLI and fail closed on claim/payoff/payout uncertainty."""
    original_argv = sys.argv
    try:
        _preflight_claim(original_argv[1:])
    except ClaimPayoffError as exc:
        if _json_requested():
            print(
                json.dumps(
                    {
                        "error": "claim_payoff_unavailable",
                        "reason_code": exc.code,
                    },
                    sort_keys=True,
                )
            )
        else:
            print(
                f"Error: claim payoff path unavailable [{exc.code}]: {exc}",
                file=sys.stderr,
            )
        raise SystemExit(2) from None
    except ClaimEconomicAdmissionError as exc:
        if _json_requested():
            print(
                json.dumps(
                    {
                        "error": "claim_economics_unavailable",
                        "reason_code": exc.code,
                    },
                    sort_keys=True,
                )
            )
        else:
            print(
                f"Error: claim economics unavailable [{exc.code}]: {exc}",
                file=sys.stderr,
            )
        raise SystemExit(2) from None
    except ClaimPreflightBlocked as exc:
        if _json_requested():
            print(json.dumps(_blocked_json(exc), sort_keys=True))
        else:
            print(f"Error: claim blocked: {exc}", file=sys.stderr)
        raise SystemExit(exc.exit_code) from None
    except (
        LiveCashAdmissionError,
        BountyAvailabilityError,
        BountyPreflightError,
        BountyAuditError,
        QualificationInputError,
        ValueError,
    ) as exc:
        if _json_requested():
            print(json.dumps({"error": "claim_preflight_unavailable"}, sort_keys=True))
        else:
            print(f"Error: claim preflight unavailable: {exc}", file=sys.stderr)
        raise SystemExit(2) from None

    sanitized_argv = [
        original_argv[0],
        *_strip_claim_wrapper_options(original_argv[1:]),
    ]
    try:
        sys.argv = sanitized_argv
        _cli_main()
    except PayoutLookupError as exc:
        print(f"Error: payout status unavailable: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    finally:
        sys.argv = original_argv
