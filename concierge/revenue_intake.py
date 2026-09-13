# SPDX-License-Identifier: MIT
"""One-command fail-closed intake gate for paid engineering work.

This composes the existing paid-work qualification gate with canonical source
provenance. Both gates must authorize dispatch. The result is deliberately safe
to log: raw issue, listing, contribution-term, and comment text is never copied
into the output.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from concierge.bounty_qualification import QualificationInputError, qualify_dispatch
from concierge.source_provenance import (
    ProvenanceInputError,
    verify_source_provenance,
)


class RevenueIntakeInputError(ValueError):
    """Raised when a combined intake snapshot is structurally unreliable."""


_DISPOSITION_RANK = {"ACTIONABLE": 0, "HOLD": 1, "REJECT": 2}


def _combined_disposition(*values: str) -> str:
    try:
        return max(values, key=_DISPOSITION_RANK.__getitem__)
    except (KeyError, TypeError) as exc:
        raise RevenueIntakeInputError("gate returned an unknown disposition") from exc


def qualify_revenue_intake(
    snapshot: dict[str, Any], *, saturation_threshold: int = 4
) -> dict[str, Any]:
    """Run qualification and provenance gates over one normalized snapshot."""
    if not isinstance(snapshot, dict):
        raise RevenueIntakeInputError("snapshot must be an object")

    qualification = qualify_dispatch(
        snapshot, saturation_threshold=saturation_threshold
    )
    provenance = verify_source_provenance(snapshot)
    disposition = _combined_disposition(
        qualification["disposition"], provenance["disposition"]
    )

    reasons: list[dict[str, str]] = []
    reason_codes: list[str] = []
    seen_codes: set[str] = set()
    for gate_name, result in (
        ("qualification", qualification),
        ("provenance", provenance),
    ):
        for reason in result.get("reasons", []):
            code = reason["code"]
            reasons.append(
                {
                    "gate": gate_name,
                    "code": code,
                    "severity": reason["severity"],
                    "message": reason["message"],
                }
            )
            qualified_code = f"{gate_name.upper()}:{code}"
            if qualified_code not in seen_codes:
                seen_codes.add(qualified_code)
                reason_codes.append(qualified_code)

    return {
        "disposition": disposition,
        "dispatch": (
            disposition == "ACTIONABLE"
            and bool(qualification.get("dispatch"))
            and bool(provenance.get("dispatch"))
        ),
        "canonical_source_url": provenance.get("use_source_url"),
        "reason_codes": reason_codes,
        "reasons": reasons,
        "qualification": {
            "disposition": qualification["disposition"],
            "dispatch": qualification["dispatch"],
            "signals": qualification.get("signals", {}),
        },
        "provenance": {
            "disposition": provenance["disposition"],
            "dispatch": provenance["dispatch"],
            "signals": provenance.get("signals", {}),
        },
    }


def format_summary(result: dict[str, Any]) -> str:
    """Format a compact safe dispatch receipt."""
    codes = ",".join(result.get("reason_codes", [])) or "none"
    source = result.get("canonical_source_url") or "none"
    return (
        f"disposition={result['disposition']} "
        f"dispatch={str(result['dispatch']).lower()} "
        f"source={source} reasons={codes}"
    )


def _load_snapshot(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        payload = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RevenueIntakeInputError("snapshot JSON must contain an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.revenue_intake",
        description=(
            "Require canonical source provenance plus paid-work qualification "
            "before implementation dispatch."
        ),
    )
    parser.add_argument("snapshot", help="JSON snapshot path, or - for stdin")
    parser.add_argument(
        "--saturation-threshold",
        type=int,
        default=4,
        help="Hold dispatch at this many attempts/open PRs (default: 4)",
    )
    parser.add_argument("--json", action="store_true", help="Emit full result JSON")
    args = parser.parse_args(argv)

    try:
        snapshot = _load_snapshot(args.snapshot)
        result = qualify_revenue_intake(
            snapshot, saturation_threshold=args.saturation_threshold
        )
    except (
        OSError,
        json.JSONDecodeError,
        QualificationInputError,
        ProvenanceInputError,
        RevenueIntakeInputError,
    ) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_summary(result))

    if result["disposition"] == "ACTIONABLE":
        return 0
    if result["disposition"] == "HOLD":
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
