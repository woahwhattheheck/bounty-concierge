# SPDX-License-Identifier: MIT
"""Recover a stopped capture batch from retained files without provider reads."""

from __future__ import annotations

import argparse
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import stat
from time import monotonic
from typing import Any

from concierge.bounty_capture import CaptureInputError, capture_digest, replay_capture
from concierge.bounty_capture_batch import _MAX_INPUT_BYTES, _now, _shortlist, _write_json
from concierge.secure_output import (
    SecureOutputError,
    create_exclusive_regular,
    open_verified_parent,
)


# Bound cache metadata; parsed captures are already retained in grouped.
_REPLAY_CACHE_ENTRIES = 128


def _read_source(path: Path, maximum: int | None = None) -> bytes:
    """Read only regular retained files, without following path symlinks."""
    parent_fd, leaf = open_verified_parent(path)
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(leaf, flags, dir_fd=parent_fd)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("retained input must be a regular file")
            raw = stream.read(-1 if maximum is None else maximum + 1)
    finally:
        os.close(parent_fd)
    if maximum is not None and len(raw) > maximum:
        raise ValueError("shortlist exceeds the capture batch input limit")
    return raw


def recover_batch(source_run: str | Path, output_dir: str | Path) -> dict[str, Any]:
    """Rebuild supply and remaining work from a manifest and valid captures.

    Existing summaries, supply files and remaining lists are never inputs.
    Original manifest/capture bytes are retained, including malformed capture
    files; only successfully replayed captures enter supply. Recovery neither
    changes observations nor reconstructs unrecorded failed provider requests.
    """
    source = Path(source_run)
    output = Path(output_dir)
    source_root = source.resolve()
    output_root = output.resolve()
    if source_root == output_root or source_root in output_root.parents:
        raise ValueError("recovery output must be outside the source run")

    started = _now()
    timer = monotonic()
    manifest = _read_source(source / "shortlist.json", _MAX_INPUT_BYTES)
    unique, duplicate_count = _shortlist(json.loads(manifest))
    indexed = {(row["repo"], row["number"]): row for row in unique}
    capture_paths = sorted(source.glob("capture-*.json"), key=lambda path: path.name)

    parent_fd, leaf = open_verified_parent(output)
    try:
        os.mkdir(leaf, mode=0o700, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    create_exclusive_regular(output / "shortlist.json", manifest, mode=0o600)

    grouped: dict[tuple[str, int], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    items: list[dict[str, Any]] = []
    # Hash every file byte locally, never the supplied receipt or issue identity.
    # Fixed-size keys allow large duplicates without retaining their raw bytes.
    # Successful replay is deterministic within this recovery invocation.
    replayed: OrderedDict[bytes, tuple[dict[str, Any], dict[str, Any]]] = OrderedDict()
    for path in capture_paths:
        item: dict[str, Any] = {
            "capture_file": path.name, "status": "RECOVERY_ERROR", "request_count": 0,
        }
        try:
            raw = _read_source(path)
        except (OSError, ValueError, SecureOutputError) as exc:
            item["error"] = {"code": "CAPTURE_READ_ERROR", "error_type": type(exc).__name__}
        else:
            # Preserve even an interrupted create; its error is explicit below.
            create_exclusive_regular(output / path.name, raw, mode=0o600)
            item["retained"] = True
            try:
                key = hashlib.sha256(raw).digest()
                cached = replayed.get(key)
                if cached is None:
                    capture = json.loads(raw)
                    _, qualification, _ = replay_capture(
                        capture, saturation_threshold=capture["policy"]["saturation_threshold"],
                    )
                else:
                    capture, qualification = cached
                    replayed.move_to_end(key)
                identity = capture["repo"], capture["number"]
                row = indexed.get(identity)
                if row is None:
                    item["error"] = {"code": "CAPTURE_NOT_IN_SHORTLIST"}
                elif capture.get("submission_target") != row.get("submission_target"):
                    item["error"] = {"code": "SUBMISSION_TARGET_MISMATCH"}
                else:
                    item.update(
                        repo=row["repo"], number=row["number"],
                        capture_sha256=capture["receipt_sha256"],
                        disposition=qualification["disposition"],
                        observation=capture["observation"],
                        captured_request_count=capture["observation"]["request_count"],
                    )
                    if "submission_target" in capture:
                        target = capture["submission_target"]
                        item["submission_repository"] = target["repository"]
                        item["submission_target_sha256"] = capture_digest(target)
                    grouped.setdefault(identity, []).append((capture, item))
                    if cached is None:
                        if len(replayed) >= _REPLAY_CACHE_ENTRIES:
                            replayed.popitem(last=False)
                        replayed[key] = (capture, qualification)
            except (json.JSONDecodeError, UnicodeError) as exc:
                item["error"] = {
                    "code": "CAPTURE_JSON_ERROR", "error_type": type(exc).__name__,
                }
            except (CaptureInputError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
                item["error"] = {"code": "CAPTURE_INVALID", "error_type": type(exc).__name__}
        items.append(item)

    captures: list[dict[str, Any]] = []
    completed: set[tuple[str, int]] = set()
    duplicate_capture_count = 0
    for identity, group in grouped.items():
        if len({capture["receipt_sha256"] for capture, _ in group}) > 1:
            # Never choose an arbitrary observation for conflicting captures.
            for _, item in group:
                item["error"] = {"code": "CONFLICTING_CAPTURES"}
        else:
            captures.append(group[0][0])
            completed.add(identity)
            group[0][1]["status"] = "CAPTURED"
            for _, item in group[1:]:
                item["status"] = "DUPLICATE"
            duplicate_capture_count += len(group) - 1

    error_count = sum("error" in item for item in items)
    remaining = [row for row in unique if (row["repo"], row["number"]) not in completed]
    complete = not remaining and not error_count
    summary = {
        "schema": "bounty-preflight-recovery/v1",
        "status": "COMPLETE" if complete else "PARTIAL",
        "complete": complete,
        "stop_reason": "RECOVERY_ERRORS" if error_count else (
            "CAPTURES_MISSING" if remaining else None
        ),
        "recovery_started_at": started,
        "recovery_completed_at": _now(),
        "elapsed_seconds": round(monotonic() - timer, 6),
        "source_shortlist_sha256": hashlib.sha256(manifest).hexdigest(),
        "input_count": len(unique) + duplicate_count,
        "unique_count": len(unique),
        "duplicate_count": duplicate_count,
        "duplicate_capture_count": duplicate_capture_count,
        "capture_file_count": len(capture_paths),
        "captured_count": len(captures),
        "remaining_count": len(remaining),
        "recovery_error_count": error_count,
        "request_count": 0,
        "captured_request_count": sum(
            capture["observation"]["request_count"] for capture in captures
        ),
        "items": items,
        "supply_file": "supply.json",
        "remaining_file": "remaining.json",
    }
    _write_json(output / "remaining.json", {"candidates": remaining})
    _write_json(output / "supply.json", {"candidates": captures})
    _write_json(output / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bounty_capture_recover",
        description="Recover retained bounty captures offline into a new private directory.",
    )
    parser.add_argument("source_run", type=Path, help="Stopped batch directory containing shortlist.json")
    parser.add_argument("--output-dir", type=Path, required=True, help="New private directory outside source")
    parser.add_argument("--json", action="store_true", help="Print the recovery summary")
    args = parser.parse_args(argv)
    try:
        result = recover_batch(args.source_run, args.output_dir)
    except (OSError, ValueError, SecureOutputError, RecursionError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            f"{result['status']} captured={result['captured_count']}/{result['unique_count']} "
            f"requests={result['request_count']} retained_requests={result['captured_request_count']} "
            f"remaining={result['remaining_count']} errors={result['recovery_error_count']} "
            f"summary={args.output_dir / 'summary.json'}"
        )
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
