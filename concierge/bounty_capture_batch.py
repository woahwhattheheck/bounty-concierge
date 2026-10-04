# SPDX-License-Identifier: MIT
"""Collect a shortlist once for the existing offline bounty supply router."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import re
import sys
from time import monotonic
from typing import Any

import requests

from concierge.bounty_audit import BountyAuditError
from concierge.bounty_capture import CaptureInputError
from concierge.bounty_preflight import BountyPreflightError, preflight_bounty
from concierge.secure_output import (
    SecureOutputError,
    create_exclusive_regular,
    open_verified_parent,
)


_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_MAX_INPUT_BYTES = 1024 * 1024
_MAX_CANDIDATES = 1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _positive(value: Any, name: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer between 1 and {maximum}")
    return value


def _shortlist(payload: Any) -> tuple[list[dict[str, Any]], int]:
    if isinstance(payload, dict) and set(payload) == {"candidates"}:
        payload = payload["candidates"]
    if not isinstance(payload, list) or len(payload) > _MAX_CANDIDATES:
        raise ValueError("shortlist must contain at most 1000 repo/number objects")
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for row in payload:
        if not isinstance(row, dict) or set(row) != {"repo", "number"}:
            raise ValueError("each shortlist row must contain only repo and number")
        repo = row["repo"]
        number = row["number"]
        if not isinstance(repo, str) or _REPO.fullmatch(repo.strip()) is None:
            raise ValueError("repo must be an owner/repository slug")
        repo = repo.strip().casefold()
        if any(part in {".", ".."} for part in repo.split("/")):
            raise ValueError("repo must be an owner/repository slug")
        if type(number) is not int or number <= 0:
            raise ValueError("number must be a positive issue number")
        identity = repo, number
        if identity not in seen:
            seen.add(identity)
            unique.append({"repo": repo, "number": number})
    return unique, len(payload) - len(unique)


def _integer_header(headers: Any, name: str) -> int | None:
    value = str(headers.get(name, "")).strip()
    return int(value) if value.isascii() and value.isdigit() and len(value) <= 12 else None


class _BatchSession:
    """Count real GETs and stop further reads after provider quota exhaustion."""

    def __init__(self, session: Any, max_requests: int) -> None:
        self.session = session
        self.max_requests = max_requests
        self.request_count = 0
        self.rate_limited = False
        self.retry_after_seconds: int | None = None
        self.retry_after_at: str | None = None
        self.rate_limit_reset_at: int | None = None
        self.failure: dict[str, Any] | None = None

    def get(self, url: str, **kwargs: Any) -> Any:
        if self.rate_limited:
            self.failure = {"code": "RATE_LIMITED"}
            raise requests.RequestException("provider quota exhausted; remaining reads deferred")
        if self.request_count >= self.max_requests:
            self.failure = {"code": "REQUEST_LIMIT"}
            raise requests.RequestException("batch request budget reached; remaining reads deferred")
        self.request_count += 1
        try:
            response = self.session.get(url, **kwargs)
        except requests.RequestException as exc:
            self.failure = {"code": "TRANSPORT_ERROR", "error_type": type(exc).__name__}
            raise
        status = response.status_code
        headers = response.headers
        remaining = _integer_header(headers, "X-RateLimit-Remaining")
        retry_after = _integer_header(headers, "Retry-After")
        retry_at = None
        raw_retry = headers.get("Retry-After")
        if retry_after is None and isinstance(raw_retry, str) and len(raw_retry) <= 128:
            try:
                parsed = parsedate_to_datetime(raw_retry)
                if parsed.tzinfo is not None:
                    retry_at = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            except (TypeError, ValueError, OverflowError):
                pass
        throttled = status == 429 or (status == 403 and (
            remaining == 0 or retry_after is not None or retry_at is not None
        ))
        if status == 403 and not throttled:
            try:
                error = response.json()
            except (TypeError, ValueError):
                error = {}
            message = error.get("message", "") if isinstance(error, dict) else ""
            throttled = isinstance(message, str) and "rate limit" in message.casefold()
        if throttled or remaining == 0:
            self.rate_limited = True
            self.retry_after_seconds = retry_after
            self.retry_after_at = retry_at
            self.rate_limit_reset_at = _integer_header(headers, "X-RateLimit-Reset")
        if status >= 400:
            self.failure = {
                "code": "RATE_LIMITED" if throttled else "HTTP_ERROR",
                "http_status": status,
            }
        return response


def _write_json(path: Path, value: Any) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    create_exclusive_regular(path, payload, mode=0o600)


def collect_batch(
    candidates: Any,
    output_dir: str | Path,
    *,
    token: str | None = None,
    session: Any = None,
    max_issues: int = 25,
    max_pages: int = 10,
    max_requests: int = 100,
    saturation_threshold: int = 4,
    operator_login: str | None = None,
) -> dict[str, Any]:
    """Save each completed preflight immediately; return a safe coverage summary.

    A caller-supplied session remains caller-owned. Otherwise exactly one
    requests.Session is created and closed for the entire batch. There are no
    automatic retries, sleeps, provider writes, or offline freshness changes.
    """
    unique, duplicate_count = _shortlist(candidates)
    _positive(max_issues, "max_issues", _MAX_CANDIDATES)
    _positive(max_pages, "max_pages", 1000)
    _positive(max_requests, "max_requests", 10000)
    _positive(saturation_threshold, "saturation_threshold", _MAX_CANDIDATES)
    if operator_login is not None and (
        not isinstance(operator_login, str) or not operator_login.strip()
    ):
        raise ValueError("operator_login must be a nonempty string when supplied")
    if session is not None and not callable(getattr(session, "get", None)):
        raise ValueError("session must provide get")

    output = Path(output_dir)
    parent_fd, leaf = open_verified_parent(output)
    try:
        os.mkdir(leaf, mode=0o700, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    # This immutable manifest survives even an abrupt termination before summary.
    _write_json(output / "shortlist.json", {"candidates": unique})
    owned = session is None
    provider = requests.Session() if owned else session
    transport = _BatchSession(provider, max_requests)
    started = _now()
    timer = monotonic()
    captures: list[dict[str, Any]] = []
    completed: set[tuple[str, int]] = set()
    items: list[dict[str, Any]] = []
    stop_reason: str | None = None
    try:
        for row in unique[:max_issues]:
            if transport.rate_limited:
                stop_reason = "RATE_LIMITED"
                break
            if transport.request_count >= max_requests:
                stop_reason = "REQUEST_LIMIT"
                break
            transport.failure = None
            item = {**row, "status": "FAILED"}
            requests_before = transport.request_count
            try:
                result = preflight_bounty(
                    row["repo"], row["number"], token,
                    session=transport, max_pages=max_pages,
                    saturation_threshold=saturation_threshold,
                    operator_login=operator_login, include_capture=True,
                )
                capture = result["capture"]
                filename = f"capture-{len(captures) + 1:04d}.json"
                _write_json(output / filename, capture)
                captures.append(capture)
                completed.add((row["repo"], row["number"]))
                item.update(
                    status="CAPTURED", capture_file=filename,
                    capture_sha256=capture["receipt_sha256"],
                    disposition=result["qualification"]["disposition"],
                    observation=capture["observation"],
                )
            except KeyboardInterrupt:
                item.update(status="INTERRUPTED", error={"code": "INTERRUPTED"})
                stop_reason = "INTERRUPTED"
            except (BountyPreflightError, BountyAuditError, CaptureInputError,
                    requests.RequestException, ValueError) as exc:
                # Exception strings can contain URLs, source prose or headers.
                error = transport.failure or {
                    "code": "PREFLIGHT_ERROR", "error_type": type(exc).__name__,
                }
                item["error"] = error
                stop_reason = error["code"]
            except (OSError, SecureOutputError) as exc:
                item["error"] = {"code": "OUTPUT_ERROR", "error_type": type(exc).__name__}
                stop_reason = "OUTPUT_ERROR"
            item["request_count"] = transport.request_count - requests_before
            items.append(item)
            if stop_reason is not None:
                break
    except KeyboardInterrupt:
        stop_reason = "INTERRUPTED"
    finally:
        if owned:
            provider.close()

    remaining = [row for row in unique if (row["repo"], row["number"]) not in completed]
    complete = not remaining
    if remaining and stop_reason is None:
        stop_reason = "RATE_LIMITED" if transport.rate_limited else "ISSUE_LIMIT"
    summary = {
        "schema": "bounty-preflight-batch/v1",
        "status": "COMPLETE" if complete else "PARTIAL",
        "complete": complete,
        "stop_reason": stop_reason,
        "started_at": started,
        "completed_at": _now(),
        "elapsed_seconds": round(monotonic() - timer, 6),
        "input_count": len(unique) + duplicate_count,
        "unique_count": len(unique),
        "duplicate_count": duplicate_count,
        "captured_count": len(captures),
        "remaining_count": len(remaining),
        "request_count": transport.request_count,
        "rate_limited": transport.rate_limited,
        "retry_after_seconds": transport.retry_after_seconds,
        "retry_after_at": transport.retry_after_at,
        "rate_limit_reset_at": transport.rate_limit_reset_at,
        "policy": {"max_issues": max_issues, "max_pages": max_pages,
                   "max_requests": max_requests,
                   "saturation_threshold": saturation_threshold},
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
        prog="python -m concierge.bounty_capture_batch",
        description="Collect a bounded GitHub issue shortlist once for offline bounty routing.",
    )
    parser.add_argument("shortlist", help="JSON repo/number list, candidates envelope, or - for stdin")
    parser.add_argument("--output-dir", type=Path, required=True, help="New private directory; never overwrite")
    parser.add_argument("--max-issues", type=int, default=25)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--max-requests", type=int, default=100, help="Maximum actual GET attempts in this run")
    parser.add_argument("--saturation-threshold", type=int, default=4)
    parser.add_argument("--operator-login", help="Optional identity assertion; existing authentication rules apply")
    parser.add_argument("--json", action="store_true", help="Print the safe collection summary")
    args = parser.parse_args(argv)
    try:
        if args.shortlist == "-":
            raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        else:
            with Path(args.shortlist).open("rb") as stream:
                raw = stream.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            raise ValueError("shortlist exceeds 1 MiB")
        result = collect_batch(
            json.loads(raw), args.output_dir, max_issues=args.max_issues,
            max_pages=args.max_pages, max_requests=args.max_requests,
            saturation_threshold=args.saturation_threshold,
            operator_login=args.operator_login,
        )
    except (OSError, ValueError, SecureOutputError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            f"{result['status']} captured={result['captured_count']}/{result['unique_count']} "
            f"duplicates={result['duplicate_count']} requests={result['request_count']} "
            f"remaining={result['remaining_count']} stop={result['stop_reason']} "
            f"summary={args.output_dir / 'summary.json'}"
        )
    if result["stop_reason"] == "INTERRUPTED":
        return 130
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
