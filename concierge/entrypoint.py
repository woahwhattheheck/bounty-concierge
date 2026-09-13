# SPDX-License-Identifier: MIT
"""Console entry point with sanitized domain-error handling."""

from __future__ import annotations

import json
import sys

from concierge.bounty_qualification import QualificationInputError
from concierge.cli import main as _cli_main
from concierge.live_claim_qualification import (
    ClaimQualificationBlocked,
    LiveQualificationError,
    format_summary as format_claim_summary,
    preflight_claim_argv,
)
from concierge.payout_tracker import PayoutLookupError


def _json_requested() -> bool:
    return "--json" in sys.argv[1:]


def main() -> None:
    """Run the CLI and fail closed on claim/payout lookup uncertainty."""
    try:
        preflight_claim_argv(sys.argv[1:])
        _cli_main()
    except ClaimQualificationBlocked as exc:
        if _json_requested():
            print(
                json.dumps(
                    {"error": "claim_blocked", "qualification": exc.result},
                    sort_keys=True,
                )
            )
        else:
            print(
                f"Error: claim blocked: {format_claim_summary(exc.result)}",
                file=sys.stderr,
            )
        raise SystemExit(exc.exit_code) from None
    except (LiveQualificationError, QualificationInputError) as exc:
        if _json_requested():
            print(json.dumps({"error": "claim_qualification_unavailable"}, sort_keys=True))
        else:
            print(
                f"Error: claim qualification unavailable: {exc}",
                file=sys.stderr,
            )
        raise SystemExit(2) from None
    except PayoutLookupError as exc:
        print(f"Error: payout status unavailable: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
