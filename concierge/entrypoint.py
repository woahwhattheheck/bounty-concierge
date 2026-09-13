# SPDX-License-Identifier: MIT
"""Console entry point with sanitized domain-error handling."""

from __future__ import annotations

import sys

from concierge.cli import main as _cli_main
from concierge.payout_tracker import PayoutLookupError


def main() -> None:
    """Run the CLI and render payout lookup failures without a traceback."""
    try:
        _cli_main()
    except PayoutLookupError as exc:
        print(f"Error: payout status unavailable: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
