# SPDX-License-Identifier: MIT
"""CLI-only helpers for the reinvestment review surface."""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from typing import Any, Dict, List, Optional, Tuple


def run_cli(
    compile_reinvestment_review,
    verify_reinvestment_receipt_current,
    ReinvestmentInputError,
    max_json_bytes: int,
    argv: Optional[List[str]] = None,
) -> int:
    _MAX_JSON_BYTES = max_json_bytes

    def _duplicate_safe_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReinvestmentInputError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result


    def _read_bounded(path: str) -> bytes:
        if path == "-":
            data = sys.stdin.buffer.read(_MAX_JSON_BYTES + 1)
            if len(data) > _MAX_JSON_BYTES:
                raise ReinvestmentInputError("stdin JSON is too large")
            return data
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise ReinvestmentInputError(f"cannot open {path}: {exc}") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ReinvestmentInputError(f"{path} must be a regular file")
            if info.st_size > _MAX_JSON_BYTES:
                raise ReinvestmentInputError(f"{path} is too large")
            chunks: List[bytes] = []
            remaining = _MAX_JSON_BYTES + 1
            while remaining:
                chunk = os.read(fd, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > _MAX_JSON_BYTES:
                raise ReinvestmentInputError(f"{path} grew beyond the size limit")
            return data
        finally:
            os.close(fd)


    def _load_json(path: str) -> Any:
        raw = _read_bounded(path)
        try:
            return json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_duplicate_safe_object,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ReinvestmentInputError(
                        f"non-finite JSON value {value} is not allowed"
                    )
                ),
            )
        except ReinvestmentInputError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReinvestmentInputError(
                f"{path} is not valid strict UTF-8 JSON"
            ) from exc


    def _schema_items(payload: Any, label: str) -> List[Dict[str, Any]]:
        if type(payload) is not dict or payload.get("schema_version") != 1:
            raise ReinvestmentInputError(
                f"{label} must be a schema_version 1 object"
            )
        items = payload.get("items")
        if type(items) is not list:
            raise ReinvestmentInputError(f"{label}.items must be a list")
        return items


    def _write_output(path: Optional[str], payload: Dict[str, Any]) -> None:
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if path is None:
            print(text, end="")
            return
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ReinvestmentInputError(
                f"refusing unsafe or existing output {path}: {exc}"
            ) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                fd = -1
                handle.write(text)
        finally:
            if fd >= 0:
                os.close(fd)


    parser = argparse.ArgumentParser(
        prog="python -m concierge.reinvestment_allocator",
        description=(
            "Verify fresh externally signed attribution/effort in an isolated "
            "worker, reacquire live GitHub closeout and canonical wallet cash, "
            "then prepare an advisory owner reinvestment review."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(target: argparse.ArgumentParser) -> None:
        target.add_argument("manifest", help="revenue-closeout manifest JSON")
        target.add_argument("bindings", help="payment bindings JSON")
        target.add_argument("effort", help="operator effort JSON")
        target.add_argument(
            "authority",
            help="fresh externally signed commercial evidence authority JSON",
        )
        target.add_argument("taxonomy", help="caller planning taxonomy JSON")
        target.add_argument("policy", help="caller planning policy JSON")
        target.add_argument("--wallet", required=True)
        target.add_argument("--capacity-minutes", required=True, type=int)
        target.add_argument("--max-closeout-pages", type=int, default=10)

    compile_parser = sub.add_parser("compile")
    add_common(compile_parser)
    compile_parser.add_argument("--output")

    verify_parser = sub.add_parser("verify-current")
    verify_parser.add_argument("receipt")
    add_common(verify_parser)

    args = parser.parse_args(argv)
    paths = [
        args.manifest,
        args.bindings,
        args.effort,
        args.authority,
        args.taxonomy,
        args.policy,
    ]
    if args.command == "verify-current":
        paths.append(args.receipt)
    if paths.count("-") > 1:
        parser.error("at most one input may read from stdin")
    try:
        manifest = _load_json(args.manifest)
        bindings = _load_json(args.bindings)
        effort = _load_json(args.effort)
        authority = _load_json(args.authority)
        taxonomy = _load_json(args.taxonomy)
        policy = _load_json(args.policy)
        manifest_items = _schema_items(manifest, "manifest")
        binding_items = _schema_items(bindings, "bindings")
        if args.command == "compile":
            payload = compile_reinvestment_review(
                manifest_items,
                binding_items,
                effort,
                authority,
                taxonomy,
                policy,
                args.capacity_minutes,
                wallet=args.wallet,
                max_closeout_pages=args.max_closeout_pages,
            )
            _write_output(args.output, payload)
            return 0
        receipt = _load_json(args.receipt)
        if not verify_reinvestment_receipt_current(
            receipt,
            manifest_items,
            binding_items,
            effort,
            authority,
            taxonomy,
            policy,
            args.capacity_minutes,
            wallet=args.wallet,
            max_closeout_pages=args.max_closeout_pages,
        ):
            raise ReinvestmentInputError("current receipt verification failed")
        print(receipt["receipt_sha256"])
        return 0
    except (OSError, ReinvestmentInputError) as exc:
        parser.error(str(exc))

