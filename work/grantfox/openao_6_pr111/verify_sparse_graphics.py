#!/usr/bin/env python3
"""Host-side oracle for OpenAO #6 / PR #111 sparse graphic existence.

This does not pretend to run the TypeScript service. It models the exact range
predicate added by PR #111 and compares it with canonical catalog membership.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

MAX_ENGINE_GRAPHIC_INDEX = 320_151
UPLOADED_GRAPHIC_INDEX_START = 1_000_000


def current_pr_accepts_as_original(graphic_id: int) -> bool:
    """Exact original-ID range decision encoded by PR #111."""
    return 0 < graphic_id <= MAX_ENGINE_GRAPHIC_INDEX


def catalog_numeric_ids(payload: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for key in payload:
        try:
            value = int(key)
        except (TypeError, ValueError):
            continue
        ids.add(value)
    return ids


def false_positive_original_ids(payload: dict[str, Any]) -> list[int]:
    ids = catalog_numeric_ids(payload)
    if not ids:
        return []
    upper = min(MAX_ENGINE_GRAPHIC_INDEX, max(ids))
    return [
        value
        for value in range(1, upper + 1)
        if current_pr_accepts_as_original(value) and value not in ids
    ]


def analyze(payload: dict[str, Any], probe: int = 5750) -> dict[str, Any]:
    ids = catalog_numeric_ids(payload)
    positives = false_positive_original_ids(payload)
    return {
        "numeric_key_count": len(ids),
        "min_numeric_key": min(ids) if ids else None,
        "max_numeric_key": max(ids) if ids else None,
        "probe": probe,
        "probe_catalog_exists": probe in ids,
        "probe_current_pr_accepts": current_pr_accepts_as_original(probe),
        "probe_false_positive": current_pr_accepts_as_original(probe) and probe not in ids,
        "false_positive_count_through_catalog_max": len(positives),
        "first_false_positives": positives[:20],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", type=Path, help="graficos_optimized.json")
    parser.add_argument("--probe", type=int, default=5750)
    args = parser.parse_args()

    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("catalog must be a JSON object")

    report = analyze(payload, args.probe)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["probe_false_positive"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
