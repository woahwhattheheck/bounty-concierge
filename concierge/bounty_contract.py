# SPDX-License-Identifier: MIT
"""Capture and verify durable, privacy-safe bounty contract receipts.

Paid-work preflight answers whether a bounty is safe to dispatch *now*. This
module protects the longer interval through implementation, submission,
acceptance, and settlement. It is read-only: no claims, comments, submissions,
provider changes, payments, or cash inferences are performed.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any

import requests

from concierge.bounty_contract_common import (
    RECEIPT_SCHEMA,
    VERIFICATION_SCHEMA,
    _AUTHORITY,
    _DRIFT_ORDER,
    BountyContractError,
    BountyContractEvidenceError,
    BountyContractInputError,
    _hash_json,
    _now_or,
)
from concierge.bounty_contract_hardening import (
    _read_stable_generation,
    validate_receipt,
)
from concierge.bounty_contract_receipt import (
    _reason_codes,
    _verification_receipt,
)


def capture_contract(
    repo: str,
    number: int,
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Capture one stable, privacy-safe claim-time bounty contract receipt."""
    observed = _read_stable_generation(
        repo,
        number,
        token,
        session=session,
        max_pages=max_pages,
    )
    core = {
        "schema": RECEIPT_SCHEMA,
        "source": observed["source"],
        "captured_at": _now_or(captured_at, "captured_at"),
        "source_updated_at": observed["source_updated_at"],
        "contract": observed["contract"],
        "contract_sha256": observed["contract_sha256"],
        "authority": deepcopy(_AUTHORITY),
    }
    return {**core, "receipt_sha256": _hash_json(core)}


def verify_contract(
    receipt: Any,
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
    checked_at: str | None = None,
) -> dict[str, Any]:
    """Verify a stored receipt against one stable current GitHub generation."""
    baseline = validate_receipt(receipt)
    observed_at = _now_or(checked_at, "checked_at")
    try:
        current = _read_stable_generation(
            baseline["source"]["repo"],
            baseline["source"]["issue"],
            token,
            session=session,
            max_pages=max_pages,
        )
    except BountyContractEvidenceError as exc:
        if exc.reason_code not in _DRIFT_ORDER:
            raise
        return _verification_receipt(
            baseline,
            checked_at=observed_at,
            disposition="HOLD",
            reason_codes=[exc.reason_code],
            live=None,
        )

    live = {
        "source": current["source"],
        "source_updated_at": current["source_updated_at"],
        "contract": current["contract"],
        "contract_sha256": current["contract_sha256"],
    }
    reasons = _reason_codes(baseline, live)
    return _verification_receipt(
        baseline,
        checked_at=observed_at,
        disposition="UNCHANGED" if not reasons else "HOLD",
        reason_codes=reasons,
        live=live,
    )


def format_summary(result: dict[str, Any]) -> str:
    if result["schema"] == RECEIPT_SCHEMA:
        source = result["source"]
        return (
            f"{source['repo']}#{source['issue']} captured "
            f"contract={result['contract_sha256']} mutation=false cash_claim=false"
        )
    source = result["source"]
    reasons = ",".join(result["reason_codes"]) or "none"
    return (
        f"{source['repo']}#{source['issue']} disposition={result['disposition']} "
        f"reasons={reasons} mutation=false cash_claim=false"
    )


def _load_json(path: str) -> Any:
    if path == "-":
        import sys

        return json.load(sys.stdin)
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _emit(value: dict[str, Any], path: str | None, *, pretty: bool) -> None:
    text = json.dumps(
        value,
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=True,
        separators=None if pretty else (",", ":"),
    )
    if path is None or path == "-":
        print(text)
        return

    # A claim-time receipt is durable evidence. Never truncate an existing path
    # and never follow a final-component symlink. O_CREAT|O_EXCL guarantees the
    # final path did not exist at creation time, including an existing symlink.
    target = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = -1
            handle.write(text)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if fd >= 0:
            os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bounty_contract",
        description="Capture or verify a read-only paid-work bounty contract receipt.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_parser = subparsers.add_parser(
        "capture", help="capture one stable bounty contract receipt"
    )
    capture_parser.add_argument("repo", help="GitHub repository in owner/name form")
    capture_parser.add_argument("issue", type=int, help="bounty issue number")
    capture_parser.add_argument("--max-pages", type=int, default=10)
    capture_parser.add_argument("--captured-at")
    capture_parser.add_argument("--output")
    capture_parser.add_argument("--compact", action="store_true")

    verify_parser = subparsers.add_parser(
        "verify", help="verify a stored receipt against live GitHub terms"
    )
    verify_parser.add_argument("receipt", help="JSON receipt path or - for stdin")
    verify_parser.add_argument("--max-pages", type=int, default=10)
    verify_parser.add_argument("--checked-at")
    verify_parser.add_argument("--output")
    verify_parser.add_argument("--compact", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            result = capture_contract(
                args.repo,
                args.issue,
                max_pages=args.max_pages,
                captured_at=args.captured_at,
            )
        else:
            result = verify_contract(
                _load_json(args.receipt),
                max_pages=args.max_pages,
                checked_at=args.checked_at,
            )
    except (BountyContractInputError, BountyContractError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    _emit(result, args.output, pretty=not args.compact)
    if result["schema"] == VERIFICATION_SCHEMA and result["disposition"] == "HOLD":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
