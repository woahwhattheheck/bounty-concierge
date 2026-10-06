# SPDX-License-Identifier: MIT
"""Collect a shortlist once for the existing offline bounty supply router."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from itertools import chain
import json
import math
import os
from pathlib import Path
import re
import sys
from time import monotonic, time
from typing import Any

import requests

from concierge.bounty_audit import BountyAuditError
from concierge.bounty_capture import CaptureInputError, capture_digest
from concierge.bounty_preflight import BountyPreflightError, preflight_bounty
from concierge.config import GITHUB_TOKEN
from concierge.github_cooldown import CooldownStateError, GitHubCooldown, cooldown_deadline
from concierge.secure_output import (
    SecureOutputError,
    create_exclusive_regular,
    create_exclusive_regular_chunks,
    open_verified_parent,
)
from concierge.submission_packet import validate_submission_target


_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_ISSUE_API = re.compile(
    r"https://api\.github\.com/(?:repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
    r"|repositories/[1-9][0-9]*)/issues/[1-9][0-9]*\Z",
    re.IGNORECASE,
)
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
    seen: dict[tuple[str, int], dict[str, str] | None] = {}
    for row in payload:
        if not isinstance(row, dict) or set(row) not in (
            {"repo", "number"}, {"repo", "number", "submission_target"},
        ):
            raise ValueError(
                "each shortlist row requires repo/number and an optional submission_target"
            )
        repo = row["repo"]
        number = row["number"]
        if not isinstance(repo, str) or _REPO.fullmatch(repo.strip()) is None:
            raise ValueError("repo must be an owner/repository slug")
        repo = repo.strip().casefold()
        if any(part in {".", ".."} for part in repo.split("/")):
            raise ValueError("repo must be an owner/repository slug")
        if type(number) is not int or number <= 0:
            raise ValueError("number must be a positive issue number")
        target = (
            validate_submission_target(
                row["submission_target"], f"https://github.com/{repo}/issues/{number}"
            )
            if "submission_target" in row else None
        )
        identity = repo, number
        if identity in seen:
            if seen[identity] != target:
                raise ValueError("conflicting submission_target records for the same issue")
        else:
            seen[identity] = target
            candidate: dict[str, Any] = {"repo": repo, "number": number}
            if target is not None:
                candidate["submission_target"] = target
            unique.append(candidate)
    return unique, len(payload) - len(unique)


def _integer_header(headers: Any, name: str) -> int | None:
    value = str(headers.get(name, "")).strip()
    return int(value) if value.isascii() and value.isdigit() and len(value) <= 12 else None


class _BatchSession:
    """Count transport calls and Requests redirect hops before dispatch."""

    def __init__(
        self, session: Any, max_requests: int, cooldown: GitHubCooldown | None = None,
        reserve_requests: int = 0,
    ) -> None:
        self.session = session
        self.max_requests = max_requests
        self.request_count = 0
        self.rate_limited = False
        self.retry_after_seconds: int | None = None
        self.retry_after_at: str | None = None
        self.rate_limit_reset_at: int | None = None
        self.failure: dict[str, Any] | None = None
        self.request_url: str | None = None
        self.request_origin_url: str | None = None
        self.issue_request = False
        self.cooldown = cooldown
        self.reserve_requests = reserve_requests
        self.request_headroom_reserved = False
        self.rate_limit_remaining: int | None = None
        self.shared_cooldown_deferred = False
        self.shared_headroom_deferred = False
        self.shared_unknown_backoff_seconds: int | None = None
        self.cooldown_state_error = False

    def get(self, url: str, **kwargs: Any) -> Any:
        self.request_origin_url = url
        self.issue_request = _ISSUE_API.fullmatch(url) is not None
        if isinstance(self.session, requests.Session) and kwargs.get("allow_redirects", True):
            return self._get_with_redirects(url, **kwargs)
        return self._request(self.session.get, url, **kwargs)

    def _request(self, send: Any, target: Any, **kwargs: Any) -> Any:
        """Apply the shared budget immediately before one transport attempt."""
        if self.rate_limited:
            self.failure = {"code": "RATE_LIMITED"}
            raise requests.RequestException("provider quota exhausted; remaining reads deferred")
        if self.request_headroom_reserved:
            self.failure = {"code": "HEADROOM_RESERVED"}
            raise requests.RequestException("provider request headroom reserved; remaining reads deferred")
        if self.request_count >= self.max_requests:
            self.failure = {"code": "REQUEST_LIMIT"}
            raise requests.RequestException("batch request budget reached; remaining reads deferred")
        if self.cooldown is not None:
            try:
                if self.reserve_requests > 0:
                    deadline, reserve_deadline = self.cooldown.deadlines()
                else:
                    deadline = self.cooldown.deadline()
                    reserve_deadline = None
            except CooldownStateError:
                self.cooldown_state_error = True
                self.failure = {"code": "COOLDOWN_STATE_ERROR"}
                raise requests.RequestException("shared cooldown unavailable; reads deferred") from None
            now = time()
            if deadline is not None and deadline > now:
                self.rate_limited = True
                self.shared_cooldown_deferred = True
                self.retry_after_seconds = math.ceil(deadline - now)
                self.failure = {"code": "RATE_LIMITED"}
                raise requests.RequestException("shared provider cooldown active; reads deferred")
            if reserve_deadline is not None and reserve_deadline > now:
                self.request_headroom_reserved = True
                self.shared_headroom_deferred = True
                self.rate_limit_reset_at = math.ceil(reserve_deadline)
                self.failure = {"code": "HEADROOM_RESERVED"}
                raise requests.RequestException(
                    "shared provider request headroom reserved; remaining reads deferred"
                )
        self.request_count += 1
        # Keep endpoint identity private for issue-local failure isolation.
        self.request_url = target if isinstance(target, str) else getattr(target, "url", None)
        # Only issue-to-issue redirects may retain issue-local error handling.
        # Once a hop leaves that scope, later redirects cannot restore it.
        self.issue_request = self.issue_request and (
            isinstance(self.request_url, str)
            and _ISSUE_API.fullmatch(self.request_url) is not None
        )
        try:
            response = send(target, **kwargs)
        except requests.RequestException as exc:
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                try:
                    self._record_response(exc.response)
                finally:
                    # Hooks run before Requests consumes the response body.
                    # Preserve the original HTTP error even if cleanup fails.
                    try:
                        exc.response.close()
                    except Exception:
                        pass
            else:
                self.failure = {"code": "TRANSPORT_ERROR", "error_type": type(exc).__name__}
            raise
        self._record_response(response)
        return response

    def _get_with_redirects(self, url: str, **kwargs: Any) -> Any:
        """Budget each Requests-prepared redirect without changing the session."""
        settings: dict[str, Any] = {}

        def remember_settings(response: Any, **effective: Any) -> None:
            # Session.get merges environment and session settings before send.
            # Keep those exact settings, including caller hooks' transport values.
            settings.clear()
            settings.update(effective)

        request_hooks = requests.Request(hooks=kwargs.get("hooks")).hooks
        hooks = requests.sessions.merge_hooks(request_hooks, self.session.hooks)
        response_hooks = hooks.get("response") or []
        if callable(response_hooks):
            response_hooks = [response_hooks]
        kwargs["hooks"] = dict(hooks, response=[*response_hooks, remember_settings])
        kwargs["allow_redirects"] = False
        response = None
        history: list[Any] = []
        try:
            response = self._request(self.session.get, url, **kwargs)
            while response.next is not None:
                response.history = history[:]
                if len(history) >= self.session.max_redirects:
                    self.failure = {"code": "TRANSPORT_ERROR", "error_type": "TooManyRedirects"}
                    raise requests.TooManyRedirects(
                        f"Exceeded {self.session.max_redirects} redirects.", response=response,
                    )
                history.append(response)
                prepared = response.next
                send_settings = settings.copy()
                # Requests prepared the URL, method, cookies and stripped auth.
                # Recover its per-hop proxy map without preparing auth again.
                send_settings["proxies"] = self.session.rebuild_proxies(
                    prepared, send_settings.get("proxies"),
                )
                response = self._request(
                    self.session.send, prepared, allow_redirects=False, **send_settings,
                )
            if history:
                response.history = history
            return response
        except BaseException as exc:
            failed_response = getattr(exc, "response", None)
            pending = [response] if response is not failed_response else []
            # _request already attempted cleanup for an HTTPError response.
            if failed_response is not None and not isinstance(exc, requests.HTTPError):
                pending.append(failed_response)
            for item in pending:
                if item is not None:
                    try:
                        item.close()
                    except Exception:
                        pass
            raise

    def _record_response(self, response: Any) -> None:
        """Classify returned responses and HTTP errors raised by session hooks."""
        status = response.status_code
        headers = response.headers
        remaining = _integer_header(headers, "X-RateLimit-Remaining")
        reset_at = _integer_header(headers, "X-RateLimit-Reset")
        retry_after = _integer_header(headers, "Retry-After")
        self.rate_limit_remaining = remaining
        self.rate_limit_reset_at = reset_at
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
            self.rate_limit_reset_at = reset_at
        unknown_secondary = (
            throttled
            and remaining != 0
            and retry_after is None
            and retry_at is None
        )
        if status >= 400:
            self.failure = {
                "code": "RATE_LIMITED" if throttled else "HTTP_ERROR",
                "http_status": status,
            }
        if (throttled or remaining == 0) and self.cooldown is not None:
            try:
                if unknown_secondary:
                    deadline = self.cooldown.extend_unknown_secondary()
                    self.shared_unknown_backoff_seconds = max(
                        0, math.ceil(deadline - time()),
                    )
                else:
                    self.cooldown.extend(cooldown_deadline(
                        retry_seconds=retry_after, retry_at=retry_at,
                        reset_at=self.rate_limit_reset_at, primary_exhausted=remaining == 0,
                    ))
            except CooldownStateError:
                # Keep the actual response and provider evidence. This batch
                # is already stopped; report that sharing its stop failed.
                self.cooldown_state_error = True

        reserve_headroom = (
            self.reserve_requests > 0
            and status in (200, 304)
            and remaining is not None
            and 0 < remaining <= self.reserve_requests
        )
        if reserve_headroom:
            self.request_headroom_reserved = True
            self.rate_limit_remaining = remaining
            self.rate_limit_reset_at = reset_at
            if self.cooldown is not None and reset_at is not None and reset_at > time():
                try:
                    self.cooldown.reserve_quota_until(float(reset_at))
                except CooldownStateError:
                    # The current successful response remains usable; report only
                    # that sibling deferral could not be shared.
                    self.cooldown_state_error = True


def _write_json(path: Path, value: Any) -> None:
    # Finish serialization before creating the leaf so invalid JSON leaves no file.
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False)
    chunk_size = 64 * 1024
    if len(payload) <= chunk_size:
        payload += "\n"
        create_exclusive_regular(path, payload.encode("utf-8"), mode=0o600)
        return
    # Default ensure_ascii makes every character one UTF-8 byte. Keep the fast
    # serializer, but avoid another output-sized newline string and byte buffer.
    chunks = (
        payload[start:start + chunk_size].encode("utf-8")
        for start in range(0, len(payload), chunk_size)
    )
    create_exclusive_regular_chunks(path, chain(chunks, (b"\n",)), mode=0o600)


def collect_batch(
    candidates: Any,
    output_dir: str | Path,
    *,
    token: str | None = None,
    session: Any = None,
    max_issues: int = 25,
    max_pages: int = 10,
    max_requests: int = 100,
    reserve_requests: int = 0,
    saturation_threshold: int = 4,
    operator_login: str | None = None,
    cooldown_file: str | Path | None = None,
) -> dict[str, Any]:
    """Save each completed preflight immediately; return a safe coverage summary.

    A caller-supplied session remains caller-owned. Otherwise exactly one
    requests.Session is created and closed for the entire batch. There are no
    automatic retries, sleeps, provider writes, or offline freshness changes.
    An optional cooldown_file shares observed quota deadlines across invocations;
    omitting it preserves per-batch-only pacing and performs no state-file I/O.
    reserve_requests is an opt-in positive remaining-request floor. A successful
    response at or below that floor returns normally, then later GETs are deferred.
    """
    unique, duplicate_count = _shortlist(candidates)
    _positive(max_issues, "max_issues", _MAX_CANDIDATES)
    _positive(max_pages, "max_pages", 1000)
    _positive(max_requests, "max_requests", 10000)
    if type(reserve_requests) is not int or not 0 <= reserve_requests <= 10000:
        raise ValueError("reserve_requests must be an integer between 0 and 10000")
    _positive(saturation_threshold, "saturation_threshold", _MAX_CANDIDATES)
    if operator_login is not None and (
        not isinstance(operator_login, str) or not operator_login.strip()
    ):
        raise ValueError("operator_login must be a nonempty string when supplied")
    if session is not None and not callable(getattr(session, "get", None)):
        raise ValueError("session must provide get")

    cooldown = (
        GitHubCooldown(cooldown_file, token or GITHUB_TOKEN)
        if cooldown_file is not None else None
    )
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
    transport = _BatchSession(
        provider, max_requests, cooldown, reserve_requests=reserve_requests,
    )
    started = _now()
    timer = monotonic()
    captures: list[dict[str, Any]] = []
    completed: set[tuple[str, int]] = set()
    items: list[dict[str, Any]] = []
    stop_reason: str | None = None
    cleanup_error: dict[str, str] | None = None
    try:
        for row in unique[:max_issues]:
            if transport.rate_limited:
                stop_reason = "RATE_LIMITED"
                break
            if transport.request_headroom_reserved:
                stop_reason = "HEADROOM_RESERVED"
                break
            if transport.request_count >= max_requests:
                stop_reason = "REQUEST_LIMIT"
                break
            transport.failure = None
            item: dict[str, Any] = {
                "repo": row["repo"], "number": row["number"], "status": "FAILED",
            }
            target = row.get("submission_target")
            if target is not None:
                item["submission_repository"] = target["repository"]
                item["submission_target_sha256"] = capture_digest(target)
            requests_before = transport.request_count
            try:
                result = preflight_bounty(
                    row["repo"], row["number"], token,
                    session=transport, max_pages=max_pages,
                    saturation_threshold=saturation_threshold,
                    operator_login=operator_login, include_capture=True,
                    submission_target=target,
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
                # A bad or unavailable issue does not invalidate independent
                # candidates. Keep its error and retry row, then continue.
                # Authentication, provider/server/transport failures and shared
                # budgets still stop the run instead of multiplying failures.
                issue_error = error["code"] == "PREFLIGHT_ERROR" or (
                    error["code"] == "HTTP_ERROR"
                    and error["http_status"] in {404, 410}
                    and transport.issue_request
                    and transport.request_origin_url is not None
                    and transport.request_origin_url.casefold() == (
                        f"https://api.github.com/repos/{row['repo']}/issues/{row['number']}"
                    ).casefold()
                )
                if not issue_error:
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
            try:
                provider.close()
            except Exception as exc:
                # Cleanup must not discard captures, retry work or quota evidence.
                # Keep exception text private and preserve the primary outcome.
                cleanup_error = {
                    "code": "SESSION_CLOSE_ERROR", "error_type": type(exc).__name__,
                }

    remaining = [row for row in unique if (row["repo"], row["number"]) not in completed]
    # Let the next bounded run reach untouched issues before retrying failures.
    # Stable sorting preserves the original order within both groups.
    attempted = {(item["repo"], item["number"]) for item in items}
    remaining.sort(key=lambda row: (row["repo"], row["number"]) in attempted)
    complete = not remaining
    if remaining and stop_reason is None:
        if len(items) == len(unique):
            stop_reason = "ITEM_ERRORS"
        else:
            if transport.rate_limited:
                stop_reason = "RATE_LIMITED"
            elif transport.request_headroom_reserved:
                stop_reason = "HEADROOM_RESERVED"
            else:
                stop_reason = "ISSUE_LIMIT"
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
        "attempted_count": len(items),
        "captured_count": len(captures),
        "failed_count": sum(item["status"] == "FAILED" for item in items),
        "remaining_count": len(remaining),
        "request_count": transport.request_count,
        "rate_limited": transport.rate_limited,
        "request_headroom_reserved": transport.request_headroom_reserved,
        "request_headroom_floor": reserve_requests,
        "rate_limit_remaining": transport.rate_limit_remaining,
        "shared_cooldown": {
            "enabled": cooldown_file is not None,
            "deferred": transport.shared_cooldown_deferred,
            "headroom_deferred": transport.shared_headroom_deferred,
            "state_error": transport.cooldown_state_error,
            "unknown_secondary_backoff_seconds": transport.shared_unknown_backoff_seconds,
        },
        "retry_after_seconds": transport.retry_after_seconds,
        "retry_after_at": transport.retry_after_at,
        "rate_limit_reset_at": transport.rate_limit_reset_at,
        "policy": {"max_issues": max_issues, "max_pages": max_pages,
                   "max_requests": max_requests, "reserve_requests": reserve_requests,
                   "saturation_threshold": saturation_threshold},
        "items": items,
        "supply_file": "supply.json",
        "remaining_file": "remaining.json",
    }
    if cleanup_error is not None:
        summary["cleanup_error"] = cleanup_error
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
    parser.add_argument(
        "--reserve-requests", type=int, default=0,
        help=(
            "Opt-in positive GitHub remaining-request floor; let the response "
            "that reaches it return, then stop before the next GET"
        ),
    )
    parser.add_argument("--saturation-threshold", type=int, default=4)
    parser.add_argument("--operator-login", help="Optional identity assertion; existing authentication rules apply")
    parser.add_argument(
        "--cooldown-file", type=Path,
        help="Opt-in shared SQLite quota deadline file; requires an existing private parent directory",
    )
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
            reserve_requests=args.reserve_requests,
            saturation_threshold=args.saturation_threshold,
            operator_login=args.operator_login, cooldown_file=args.cooldown_file,
        )
    except (OSError, ValueError, SecureOutputError) as exc:
        parser.error(str(exc))
    if result.get("cleanup_error"):
        print("warning: SESSION_CLOSE_ERROR; capture results retained in summary", file=sys.stderr)
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
