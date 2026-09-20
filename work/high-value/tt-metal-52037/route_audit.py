#!/usr/bin/env python3
"""Static route-completeness audit for tenstorrent/tt-metal issue #52037.

The existing numeric oracle proves the stable logaddexp identities.  This
module answers a different question required by issue criterion 4: do all
three source composition sites, for both LOGADDEXP and LOGADDEXP2, stop using
the legacy EXP/EXP2 -> ADD -> LOG/LOG2 pipeline?

This is source-shape evidence only.  It is not device, accuracy, performance,
assignment, bounty, merge, or payout evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Iterable

BINARY_NG_PATH = Path(
    "ttnn/cpp/ttnn/operations/eltwise/binary_ng/device/binary_ng_utils.cpp"
)
COMMON_PATH = Path(
    "ttnn/cpp/ttnn/operations/eltwise/binary/common/binary_op_utils.cpp"
)
OPS = ("LOGADDEXP", "LOGADDEXP2")

_CASE_START = re.compile(
    r"(?m)^\s*case\s+BinaryOpType::(LOGADDEXP2|LOGADDEXP)\s*:"
)
_NEXT_CASE = re.compile(r"(?m)^\s*(?:case\s+BinaryOpType::|default\s*:)")


@dataclass(frozen=True)
class RouteFinding:
    site: str
    op: str
    legacy: bool
    reason: str
    matched_tokens: tuple[str, ...]


def strip_cpp_comments(text: str) -> str:
    """Replace C/C++ comments with whitespace while preserving newlines."""
    pattern = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)

    def repl(match: re.Match[str]) -> str:
        value = match.group(0)
        return "".join("\n" if ch == "\n" else " " for ch in value)

    return pattern.sub(repl, text)


def extract_case_blocks(text: str, op: str) -> list[str]:
    """Return blocks for one BinaryOpType case, stopping at the next case."""
    if op not in OPS:
        raise ValueError(f"unsupported op: {op}")
    clean = strip_cpp_comments(text)
    starts = [m for m in _CASE_START.finditer(clean) if m.group(1) == op]
    blocks: list[str] = []
    for match in starts:
        rest = clean[match.end():]
        next_case = _NEXT_CASE.search(rest)
        end = match.end() + (next_case.start() if next_case else len(rest))
        blocks.append(clean[match.start():end])
    return blocks


def _legacy_tokens(op: str, add_token: str) -> tuple[tuple[str, str], ...]:
    if op == "LOGADDEXP":
        return (
            (r"UnaryOpType::EXP\b", "UnaryOpType::EXP"),
            (re.escape(add_token), add_token),
            (r"UnaryOpType::LOG\b", "UnaryOpType::LOG"),
        )
    if op == "LOGADDEXP2":
        return (
            (r"UnaryOpType::EXP2\b", "UnaryOpType::EXP2"),
            (re.escape(add_token), add_token),
            (r"UnaryOpType::LOG2\b", "UnaryOpType::LOG2"),
        )
    raise ValueError(f"unsupported op: {op}")


def _finding(site: str, op: str, block: str, add_token: str) -> RouteFinding:
    specs = _legacy_tokens(op, add_token)
    matched = tuple(label for pattern, label in specs if re.search(pattern, block))
    legacy = len(matched) == len(specs)
    if legacy:
        reason = "legacy exponential-add-log composition is reachable in this case block"
    else:
        reason = "no complete legacy exponential-add-log token chain detected"
    return RouteFinding(site, op, legacy, reason, matched)


def audit_source_texts(binary_ng_text: str, common_text: str) -> dict[str, object]:
    """Audit the six issue-named route/op pairs, failing closed on shape drift."""
    errors: list[str] = []
    routes: list[RouteFinding] = []

    for op in OPS:
        blocks = extract_case_blocks(binary_ng_text, op)
        if len(blocks) != 1:
            errors.append(
                f"binary_ng:{op}: expected exactly 1 case block, found {len(blocks)}"
            )
        elif blocks:
            routes.append(_finding("binary_ng", op, blocks[0], "EnumT::ADD"))

        common_blocks = extract_case_blocks(common_text, op)
        if len(common_blocks) != 2:
            errors.append(
                f"common:{op}: expected exactly 2 case blocks, found {len(common_blocks)}"
            )
        else:
            routes.append(_finding("common_fpu", op, common_blocks[0], "add_tiles"))
            routes.append(
                _finding("common_sfpu", op, common_blocks[1], "add_binary_tile")
            )

    legacy_routes = [r for r in routes if r.legacy]
    return {
        "ok": not errors and not legacy_routes and len(routes) == 6,
        "errors": errors,
        "routes": [asdict(route) for route in routes],
        "legacy_route_count": len(legacy_routes),
    }


def audit_source_root(source_root: Path) -> dict[str, object]:
    binary_path = source_root / BINARY_NG_PATH
    common_path = source_root / COMMON_PATH
    missing = [str(path) for path in (binary_path, common_path) if not path.is_file()]
    if missing:
        return {
            "ok": False,
            "errors": [f"missing required source path: {path}" for path in missing],
            "routes": [],
            "legacy_route_count": 0,
        }
    return audit_source_texts(
        binary_path.read_text(encoding="utf-8"),
        common_path.read_text(encoding="utf-8"),
    )


def _cli(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="tt-metal checkout root to audit",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = audit_source_root(args.source_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
