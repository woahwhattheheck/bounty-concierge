#!/usr/bin/env python3
"""Source-tree reachability audit for tt-metal #56277.

This is a host-only scanner: it proves textual/configuration reachability, not device
numerics.  It intentionally does *not* flag `legacy_reduction` or `use_welford`,
which are independent controls in the assigned carrier.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import re
from typing import Iterable

TEXT_SUFFIXES = {".c", ".cc", ".cpp", ".h", ".hpp", ".inl", ".py", ".md", ".yaml", ".yml", ".json"}

PATTERNS = {
    "legacy_rsqrt_config": re.compile(r"\blegacy_rsqrt\b"),
    "legacy_compat_template": re.compile(r"\blegacy_compat\b"),
    "compat_header_reference": re.compile(r"ckernel_sfpu_rsqrt_compat\.h"),
    "legacy_bool_rsqrt_init": re.compile(r"rsqrt_tile_init\s*<\s*(?:true|false)\s*>", re.I),
    "legacy_bool_rsqrt_call": re.compile(r"rsqrt_tile\s*<\s*(?:true|false)(?:\s*,|\s*>)", re.I),
    "legacy_bool_recip_init": re.compile(r"recip_tile_init\s*<\s*(?:true|false)(?:\s*,|\s*>)", re.I),
    "legacy_bool_recip_call": re.compile(r"recip_tile\s*<\s*(?:true|false)(?:\s*,|\s*>)", re.I),
}

# PR #56292 deliberately retains only a Python compatibility keyword: accepted,
# deprecated and ignored.  It no longer selects device math.  Keep this exception
# explicit so the audit cannot silently expand it to model/config/kernel callers.
DEFAULT_DEPRECATION_ALLOWLIST = {
    "ttnn/cpp/ttnn/operations/normalization/layernorm/layernorm_nanobind.cpp",
}

@dataclass(frozen=True)
class Hit:
    rule: str
    path: str
    line: int
    text: str
    allowed_deprecation_shim: bool


def iter_text_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in {".git", "build", "generated", "third_party"} for part in path.parts):
            continue
        yield path


def audit(root: Path, *, allow_deprecation_shim: bool = False) -> list[Hit]:
    root = root.resolve()
    hits: list[Hit] = []
    for path in iter_text_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_no, text in enumerate(lines, 1):
            for rule, pattern in PATTERNS.items():
                if not pattern.search(text):
                    continue
                allowed = bool(
                    allow_deprecation_shim
                    and rule == "legacy_rsqrt_config"
                    and rel in DEFAULT_DEPRECATION_ALLOWLIST
                )
                hits.append(Hit(rule, rel, line_no, text.strip()[:240], allowed))
    return hits


def summarize(hits: Iterable[Hit]) -> dict[str, object]:
    hits = list(hits)
    by_rule: dict[str, int] = {}
    by_path: dict[str, int] = {}
    blocking = 0
    allowed = 0
    for hit in hits:
        by_rule[hit.rule] = by_rule.get(hit.rule, 0) + 1
        by_path[hit.path] = by_path.get(hit.path, 0) + 1
        if hit.allowed_deprecation_shim:
            allowed += 1
        else:
            blocking += 1
    return {
        "total_hits": len(hits),
        "blocking_hits": blocking,
        "allowed_deprecation_hits": allowed,
        "by_rule": dict(sorted(by_rule.items())),
        "by_path": dict(sorted(by_path.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, help="tt-metal checkout root")
    parser.add_argument("--allow-deprecation-shim", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    hits = audit(args.root, allow_deprecation_shim=args.allow_deprecation_shim)
    summary = summarize(hits)
    if args.json:
        print(json.dumps({"summary": summary, "hits": [asdict(h) for h in hits]}, indent=2, sort_keys=True))
    else:
        for hit in hits:
            flag = "ALLOW" if hit.allowed_deprecation_shim else "BLOCK"
            print(f"{flag}\t{hit.rule}\t{hit.path}:{hit.line}\t{hit.text}")
        print(json.dumps(summary, sort_keys=True))
    return 1 if summary["blocking_hits"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
