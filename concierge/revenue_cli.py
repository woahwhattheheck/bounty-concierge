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
from types import MappingProxyType
from typing import Callable, Mapping, NamedTuple, Sequence, TextIO


class RevenueCommand(NamedTuple):
    """One public launcher target backed by one canonical authority module."""

    module: str
    summary: str


def _build_fixed_registry():
    """Return an immutable primitive routing generation plus public discovery."""

    # Routing authority contains immutable primitives only.  Public discovery
    # objects are constructed separately below, so callers never receive an
    # object whose identity is consulted by dispatch or discovery.
    routes: tuple[tuple[str, str, str], ...] = (
        (
            "cash-cycle",
            "concierge.cash_cycle_review",
            "Compile/verify evidence-bound cash-cycle owner review receipts.",
        ),
        (
            "closeout",
            "concierge.revenue_closeout",
            "Compile/verify revenue closeout authority.",
        ),
        (
            "collection-request",
            "concierge.collection_request",
            "Compile/verify collection request packets.",
        ),
        (
            "contract-qualification",
            "concierge.contract_qualification",
            "Qualify source-backed external paid-contract opportunities.",
        ),
        (
            "distribution-fulfillment",
            "concierge.distribution_fulfillment",
            "Compile/verify distribution fulfillment evidence.",
        ),
        (
            "payoff-path",
            "concierge.payoff_path_gate",
            "Gate speculative work on an evidence-backed route to compensation.",
        ),
        (
            "payout-dispute",
            "concierge.payout_dispute",
            "Compile/verify payout dispute evidence and owner review state.",
        ),
        (
            "payout-escalation",
            "concierge.payout_escalation",
            "Compile/verify evidence-bound payout escalation state.",
        ),
        (
            "realized-economics",
            "concierge.realized_unit_economics",
            "Compute/verify realized unit economics from bound evidence.",
        ),
        (
            "receivables-aging",
            "concierge.receivables_aging",
            "Compile/verify receivables aging and collection priority evidence.",
        ),
        (
            "settlement",
            "concierge.revenue_settlement",
            "Compile/verify revenue settlement authority.",
        ),
    )

    public_commands: Mapping[str, RevenueCommand] = MappingProxyType(
        {
            target: RevenueCommand(module, summary)
            for target, module, summary in routes
        }
    )

    def rows() -> list[dict[str, str]]:
        return [
            {"target": target, "module": module, "summary": summary}
            for target, module, summary in sorted(routes)
        ]

    def names() -> tuple[str, ...]:
        return tuple(sorted(target for target, _module, _summary in routes))

    def lookup_module(target: str) -> str | None:
        for name, module, _summary in routes:
            if name == target:
                return module
        return None

    return public_commands, rows, names, lookup_module


# ``COMMANDS`` is an immutable inspection view containing independent immutable
# records. Dispatch and discovery close over a separate primitive route tuple,
# so neither mapping rebinding nor value-object tricks can redirect execution.
COMMANDS, _command_rows, _command_names, _lookup_module = _build_fixed_registry()
del _build_fixed_registry


def _print_help(stream: TextIO) -> None:
    rows = _command_rows()
    print("usage: concierge-revenue TARGET [TARGET_ARGS ...]", file=stream)
    print("", file=stream)
    print("Revenue control-plane launcher. It delegates to existing canonical", file=stream)
    print("authority modules; this launcher itself performs no provider action.", file=stream)
    print("", file=stream)
    print("targets:", file=stream)
    width = max(len(row["target"]) for row in rows)
    for row in rows:
        print(f"  {row['target']:<{width}}  {row['summary']}", file=stream)
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


def _load_main(module_name: str) -> Callable[[list[str]], object]:
    module = importlib.import_module(module_name)
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
        for target in _command_names():
            print(target, file=stdout)
        return 0

    if args[0] == "--list-json":
        print(json.dumps({"targets": _command_rows()}, sort_keys=True), file=stdout)
        return 0

    target = args[0]
    module_name = _lookup_module(target)
    if module_name is None:
        print(f"Error: unknown revenue target: {target}", file=stderr)
        print("Run 'concierge-revenue --list' for supported targets.", file=stderr)
        return 2

    # Only the captured primitive route generation can reach importlib: user
    # input is never used as a module path. Keep every ordinary import/entrypoint
    # resolution failure source-text-free because exception strings can contain
    # machine paths, environment values, or credential-like data. BaseException
    # control flow (for example KeyboardInterrupt/SystemExit) is untouched.
    try:
        entrypoint = _load_main(module_name)
    except Exception:
        print(
            f"Error: revenue target unavailable: {target} ({module_name})",
            file=stderr,
        )
        return 2

    # Invoke outside the launcher's import and return-contract handlers.
    # Downstream exceptions (including a TypeError with identical text) remain
    # the target's own semantics and are never reclassified by this wrapper.
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
