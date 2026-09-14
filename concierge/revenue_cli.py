# SPDX-License-Identifier: MIT
"""Stable control-plane launcher for shipped revenue authority modules.

This module intentionally contains no buyer, provider, payout, or revenue logic.
It only delegates an explicit target and the exact remaining argv to an existing
module ``main(argv)`` entry point.
"""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence, TextIO


@dataclass(frozen=True)
class RevenueCommand:
    """One public launcher target backed by one canonical authority module."""

    module: str
    summary: str


COMMANDS: Mapping[str, RevenueCommand] = {
    "cash-cycle": RevenueCommand(
        "concierge.cash_cycle_review",
        "Compile/verify evidence-bound cash-cycle owner review receipts.",
    ),
    "closeout": RevenueCommand(
        "concierge.revenue_closeout",
        "Compile/verify revenue closeout authority.",
    ),
    "collection-request": RevenueCommand(
        "concierge.collection_request",
        "Compile/verify collection request packets.",
    ),
    "contract-qualification": RevenueCommand(
        "concierge.contract_qualification",
        "Qualify source-backed external paid-contract opportunities.",
    ),
    "distribution-fulfillment": RevenueCommand(
        "concierge.distribution_fulfillment",
        "Compile/verify distribution fulfillment evidence.",
    ),
    "payoff-path": RevenueCommand(
        "concierge.payoff_path_gate",
        "Gate speculative work on an evidence-backed route to compensation.",
    ),
    "payout-dispute": RevenueCommand(
        "concierge.payout_dispute",
        "Compile/verify payout dispute evidence and owner review state.",
    ),
    "payout-escalation": RevenueCommand(
        "concierge.payout_escalation",
        "Compile/verify evidence-bound payout escalation state.",
    ),
    "realized-economics": RevenueCommand(
        "concierge.realized_unit_economics",
        "Compute/verify realized unit economics from bound evidence.",
    ),
    "receivables-aging": RevenueCommand(
        "concierge.receivables_aging",
        "Compile/verify receivables aging and collection priority evidence.",
    ),
    "settlement": RevenueCommand(
        "concierge.revenue_settlement",
        "Compile/verify revenue settlement authority.",
    ),
}


def _command_rows() -> list[dict[str, str]]:
    return [
        {"target": target, "module": command.module, "summary": command.summary}
        for target, command in sorted(COMMANDS.items())
    ]


def _print_help(stream: TextIO) -> None:
    print("usage: concierge-revenue TARGET [TARGET_ARGS ...]", file=stream)
    print("", file=stream)
    print("Revenue control-plane launcher. It delegates to existing canonical", file=stream)
    print("authority modules; this launcher itself performs no provider action.", file=stream)
    print("", file=stream)
    print("targets:", file=stream)
    width = max(len(name) for name in COMMANDS)
    for name, command in sorted(COMMANDS.items()):
        print(f"  {name:<{width}}  {command.summary}", file=stream)
    print("", file=stream)
    print("control-plane options:", file=stream)
    print("  -h, --help   show this help", file=stream)
    print("  --list       list target names", file=stream)
    print("  --list-json  emit target metadata as JSON", file=stream)
    print("", file=stream)
    print("All arguments after TARGET are passed through unchanged.", file=stream)
    print("Example: concierge-revenue cash-cycle compile closeout.json history.json", file=stream)
    print("         bindings.json policy.json receipt.json", file=stream)


def _normalize_exit_code(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("target main(argv) must return int or None")
    return value


def _load_main(command: RevenueCommand) -> Callable[[list[str]], object]:
    module = importlib.import_module(command.module)
    entrypoint = getattr(module, "main", None)
    if not callable(entrypoint):
        raise AttributeError("target module does not expose callable main")
    return entrypoint


def run(
    argv: Sequence[str],
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Route one fixed target to its canonical ``main(argv)`` entry point."""

    args = list(argv)
    if not args or args[0] in {"-h", "--help"}:
        _print_help(stdout)
        return 0

    if args[0] == "--list":
        for target in sorted(COMMANDS):
            print(target, file=stdout)
        return 0

    if args[0] == "--list-json":
        print(json.dumps({"targets": _command_rows()}, sort_keys=True), file=stdout)
        return 0

    target = args[0]
    command = COMMANDS.get(target)
    if command is None:
        print(f"Error: unknown revenue target: {target}", file=stderr)
        print("Run 'concierge-revenue --list' for supported targets.", file=stderr)
        return 2

    # Only fixed registry entries can reach importlib: user input is never used
    # as a module path. Keep import failure output source-text-free because
    # nested exception strings can include environment paths or credentials.
    try:
        entrypoint = _load_main(command)
    except (ImportError, AttributeError):
        print(
            f"Error: revenue target unavailable: {target} ({command.module})",
            file=stderr,
        )
        return 2

    # Invoke outside the launcher's return-contract handler. Downstream
    # exceptions (including a TypeError with identical text) remain the target's
    # own semantics and are never reclassified by this wrapper.
    result = entrypoint(args[1:])
    try:
        return _normalize_exit_code(result)
    except TypeError:
        print(f"Error: invalid return contract from revenue target: {target}", file=stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
