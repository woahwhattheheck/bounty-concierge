#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read provider-observation JSON from disk and print offline portal-registration audit."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from concierge.portal_registration_gap import audit, PortalRegistrationError


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True, help="Captured and normalized PR+portal evidence JSON")
    p.add_argument("--as-of", help="Canonical UTC seconds, e.g. 2026-10-09T05:45:00Z")
    p.add_argument("--max-age-seconds", type=int, default=86_400)
    args = p.parse_args(argv)
    try:
        path = Path(args.input)
        if path.stat().st_size > 2 * 1024 * 1024:
            raise PortalRegistrationError("input exceeds 2 MiB")
        raw = json.loads(path.read_text(encoding="utf-8"))
        observed = None
        if args.as_of is not None:
            if not args.as_of.endswith("Z"):
                raise PortalRegistrationError("as-of must be UTC seconds ending in Z")
            observed = datetime.fromisoformat(args.as_of[:-1] + "+00:00")
            if observed.microsecond or observed.tzinfo != timezone.utc:
                raise PortalRegistrationError("as-of must be canonical UTC seconds")
        output = audit(raw, as_of=observed, max_age_seconds=args.max_age_seconds)
        print(json.dumps(output, sort_keys=True, indent=2))
        return 0
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"portal audit: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
