# SPDX-License-Identifier: MIT
"""CLI for fail-closed, offline sponsor acceptance liquidity on new bounty builds."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from concierge._sponsor_liquidity_input import INPUT_SCHEMA, SponsorLiquidityInputError
from concierge._sponsor_liquidity_decision import compile_liquidity_gate, verify_receipt

__all__ = ("INPUT_SCHEMA", "SponsorLiquidityInputError", "compile_liquidity_gate", "verify_receipt", "main")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="Exhaustive canonical GitHub PR census JSON")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = compile_liquidity_gate(json.loads(Path(args.snapshot).read_text(encoding="utf-8")))
    except (OSError, SponsorLiquidityInputError, ValueError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        c = receipt["counts"]
        print(f"{receipt['disposition']} {receipt['input']['repository']} "
              f"actor_open={c['actor_open']} actor_merged={c['actor_merged_in_window']}")
    return 0 if receipt["disposition"] == "REVIEW_OTHER_GATES" else 2


if __name__ == "__main__":
    raise SystemExit(main())
