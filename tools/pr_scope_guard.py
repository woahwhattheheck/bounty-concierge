#!/usr/bin/env python3
"""Fail closed when actual changed-file scope differs from an expected manifest."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class ScopeResult:
    status: str
    expected_count: int
    actual_count: int
    unexpected: list[str]
    missing: list[str]


def normalize(paths: Iterable[str]) -> list[str]:
    return sorted({path.strip() for path in paths if path.strip()})


def read_manifest(path: str | Path) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]


def compare_scope(expected: Iterable[str], actual: Iterable[str]) -> ScopeResult:
    expected_set = set(normalize(expected))
    actual_set = set(normalize(actual))
    unexpected = sorted(actual_set - expected_set)
    missing = sorted(expected_set - actual_set)
    return ScopeResult(
        status="ok" if not unexpected and not missing else "scope_mismatch",
        expected_count=len(expected_set),
        actual_count=len(actual_set),
        unexpected=unexpected,
        missing=missing,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare expected and actual changed-file manifests exactly."
    )
    parser.add_argument("--expected-file", required=True, metavar="FILE")
    parser.add_argument("--actual-file", required=True, metavar="FILE")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    expected = read_manifest(args.expected_file)
    actual = read_manifest(args.actual_file)
    result = compare_scope(expected, actual)
    payload = {
        "expected_file": args.expected_file,
        "actual_file": args.actual_file,
        **asdict(result),
        "actual": normalize(actual),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
