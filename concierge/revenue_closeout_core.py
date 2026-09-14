# SPDX-License-Identifier: MIT
"""Fail-closed compatibility facade for the paid-work closeout core.

The historical provider/coherence implementation is retained in
``_revenue_closeout_core_impl`` for the hardened public module to compose.
This module is intentionally *not* a second positive closeout evaluator:
calling it directly fails closed until ``concierge.revenue_closeout`` installs
the authoritative validator/scanner seams.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import requests

from concierge import _revenue_closeout_core_impl as _impl


# Preserve the historical helper/test-facing surface, including single-underscore
# provider/coherence helpers.  Guarded evaluator entrypoints are replaced below.
for _export_name in dir(_impl):
    if not _export_name.startswith("__"):
        globals()[_export_name] = getattr(_impl, _export_name)


_DIRECT_CORE_ERROR = (
    "revenue_closeout_core is a non-authoritative implementation surface; "
    "use concierge.revenue_closeout for closeout evaluation"
)


def scan_paid_pr(
    raw_item: dict[str, Any],
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
) -> dict[str, Any]:
    """Fail closed unless the hardened public module replaces this seam."""
    raise RevenueCloseoutError(_DIRECT_CORE_ERROR)


def build_closeout_queue(
    items: list[dict[str, Any]],
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
) -> list[dict[str, Any]]:
    """Queue through the module-global authoritative scanner seam.

    ``concierge.revenue_closeout`` replaces ``_validate_item`` and
    ``scan_paid_pr`` on this module after importing the historical helpers.
    Direct use therefore fails closed; public-module use retains the established
    deterministic queue behavior.
    """
    if not isinstance(items, list):
        raise RevenueCloseoutInputError("items must be a list")
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
        raise RevenueCloseoutInputError("max_pages must be positive")
    seen: set[tuple[str, int]] = set()
    manifest_order: dict[tuple[str, int], int] = {}
    results: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        validated = _validate_item(raw)
        identity = (validated["repo"].casefold(), validated["pr"])
        if identity in seen:
            raise RevenueCloseoutInputError(
                f"duplicate closeout item: {validated['repo']}#{validated['pr']}"
            )
        seen.add(identity)
        manifest_order[identity] = index
        results.append(scan_paid_pr(raw, token, session=session, max_pages=max_pages))

    results.sort(
        key=lambda result: (
            _ACTION_ORDER[result["next_action"]],
            manifest_order[(result["repo"].casefold(), result["pr"])],
        )
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.revenue_closeout_core",
        description=(
            "Compatibility facade. Direct closeout evaluation is fail-closed; "
            "use python -m concierge.revenue_closeout instead."
        ),
    )
    parser.add_argument("manifest", help="JSON manifest path, or - for stdin")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="emit full safe JSON")
    args = parser.parse_args(argv)
    try:
        payload = _load_manifest(args.manifest)
        results = build_closeout_queue(payload["items"], max_pages=args.max_pages)
    except (
        OSError,
        json.JSONDecodeError,
        RevenueCloseoutError,
        RevenueCloseoutInputError,
    ) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps({"schema_version": 1, "items": results}, indent=2, sort_keys=True))
    else:
        print(format_summary(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
