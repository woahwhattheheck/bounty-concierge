"""Public CLI with host-authority verification on sponsor-event evidence."""
from __future__ import annotations

import argparse
import json
from typing import Sequence

from .artifacts import write_artifacts
from .common import AdjudicationError, load_strict
from .verified import verify_artifacts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile or verify sponsor adjudication custody evidence")
    sub = parser.add_subparsers(dest="command", required=True)
    compile_p = sub.add_parser("compile", help="compile a strict host-authorized evidence manifest")
    compile_p.add_argument("manifest")
    compile_p.add_argument("--out-dir", required=True)
    verify_p = sub.add_parser("verify", help="verify emitted artifacts and retained host authority")
    verify_p.add_argument("out_dir")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "compile":
            manifest = load_strict(args.manifest)
            hashes = write_artifacts(manifest, args.out_dir)
            print(json.dumps({"ok": True, "files": hashes}, sort_keys=True, separators=(",", ":")))
            return 0
        receipt = verify_artifacts(args.out_dir)
        print(json.dumps({"ok": True, "receipt_sha256": receipt["receipt_sha256"]}, sort_keys=True, separators=(",", ":")))
        return 0
    except (AdjudicationError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
