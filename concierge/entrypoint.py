# SPDX-License-Identifier: MIT
"""Console entry point with sanitized domain-error handling."""

from __future__ import annotations

import json
import sys
from typing import Any

from concierge.bounty_audit import BountyAuditError
from concierge.bounty_preflight import BountyPreflightError, preflight_bounty
from concierge.bounty_qualification import QualificationInputError
from concierge.cli import main as _cli_main
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


def _claim_target(argv: list[str]) -> tuple[str, int] | None:
    """Extract a valid live claim target; leave syntax errors to the main CLI."""
    try:
        command_index = argv.index("claim")
    except ValueError:
        return None
    tail = argv[command_index + 1 :]
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


def _preflight_claim(argv: list[str]) -> None:
    """Require canonical ACTIONABLE state before claim instructions are emitted."""
    target = _claim_target(argv)
    if target is None:
        return
    repo, issue = target
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
    if qualification["dispatch"]:
        return

    audit = result.get("canonical_audit")
    if not isinstance(audit, dict):
        audit = {}
    attempt_count = result.get("attempt_count")
    if isinstance(attempt_count, bool) or not isinstance(attempt_count, int):
        attempt_count = None
    open_pr_count = audit.get("open_pr_count")
    if isinstance(open_pr_count, bool) or not isinstance(open_pr_count, int):
        open_pr_count = None
    raise ClaimPreflightBlocked(
        repo=repo,
        issue=issue,
        qualification=qualification,
        attempt_count=attempt_count,
        open_pr_count=open_pr_count,
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
    """Run the CLI and fail closed on claim/payout lookup uncertainty."""
    try:
        _preflight_claim(sys.argv[1:])
    except ClaimPreflightBlocked as exc:
        if _json_requested():
            print(json.dumps(_blocked_json(exc), sort_keys=True))
        else:
            print(f"Error: claim blocked: {exc}", file=sys.stderr)
        raise SystemExit(exc.exit_code) from None
    except (
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

    try:
        _cli_main()
    except PayoutLookupError as exc:
        print(f"Error: payout status unavailable: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
