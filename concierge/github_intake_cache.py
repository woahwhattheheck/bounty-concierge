# SPDX-License-Identifier: MIT
"""Cheap-first GitHub bounty intake cache and 403 classifier."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterator

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_SHA = re.compile(r"[0-9a-fA-F]{40,64}\Z")
_REASON = re.compile(r"[A-Z0-9][A-Z0-9_.:-]{0,95}\Z")
_DISPOSITIONS = {"CARRIER", "EXCLUDE"}
_SOURCES = {"slack", "public", "github", "manual"}
_DEFAULT_TTL_SECONDS = 1800
_MAX_TTL_SECONDS = 7 * 24 * 60 * 60


class IntakeCacheError(RuntimeError):
    """Shared intake state is unavailable or malformed."""


def normalize_repo(repo: str) -> str:
    if not isinstance(repo, str) or _REPO.fullmatch(repo.strip()) is None:
        raise ValueError("repo must be an owner/repository slug")
    normalized = repo.strip().casefold()
    if any(part in {".", ".."} for part in normalized.split("/")):
        raise ValueError("repo must be an owner/repository slug")
    return normalized


def normalize_issue(issue: int) -> int:
    if isinstance(issue, bool) or not isinstance(issue, int) or issue <= 0:
        raise ValueError("issue must be a positive integer")
    return issue


def classify_github_403(
    message: str,
    *,
    remaining: int | None = None,
    retry_after_seconds: int | None = None,
) -> str:
    """Classify a GitHub 403 without returning or retaining provider text."""
    if not isinstance(message, str):
        raise ValueError("message must be a string")
    for value, name in (
        (remaining, "remaining"),
        (retry_after_seconds, "retry_after_seconds"),
    ):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ValueError(f"{name} must be a non-negative integer")

    normalized = message.casefold()
    if "resource not accessible by integration" in normalized:
        return "SCOPE_DENIED"
    if remaining == 0 or "api rate limit exceeded" in normalized:
        return "PRIMARY_RATE_LIMITED"
    if (
        "secondary rate limit" in normalized
        or "secondary rate" in normalized
        or (
            ("rate limit" in normalized or retry_after_seconds is not None)
            and remaining != 0
        )
    ):
        return "SECONDARY_RATE_LIMITED"
    if "bad credentials" in normalized or "requires authentication" in normalized:
        return "AUTH_DENIED"
    return "FORBIDDEN"


class GitHubIntakeCache:
    """SQLite-backed positive suppression observations for bounty intake."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._schema_ready = False

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            parent = self.path.parent
            if not parent.exists() or not parent.is_dir():
                raise IntakeCacheError("intake cache parent directory does not exist")
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
            connection = sqlite3.connect(self.path, timeout=1.0)
            if not self._schema_ready:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS github_intake_cache_v1 ("
                    "repo TEXT NOT NULL, issue INTEGER NOT NULL, "
                    "disposition TEXT NOT NULL, pr_number INTEGER, head_sha TEXT, "
                    "source TEXT NOT NULL, reason_code TEXT NOT NULL, "
                    "observed_epoch REAL NOT NULL, expires_epoch REAL NOT NULL, "
                    "PRIMARY KEY(repo, issue))"
                )
                connection.commit()
                self._schema_ready = True
            with connection:
                yield connection
        except IntakeCacheError:
            raise
        except (OSError, sqlite3.Error, ValueError, OverflowError) as exc:
            raise IntakeCacheError("shared GitHub intake cache unavailable") from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _validated(
        disposition: str,
        source: str,
        reason_code: str,
        pr_number: int | None,
        head_sha: str | None,
        ttl_seconds: int,
    ) -> tuple[str, str, str, int | None, str | None, int]:
        disposition = disposition.strip().upper()
        source = source.strip().casefold()
        reason_code = reason_code.strip().upper()
        if disposition not in _DISPOSITIONS:
            raise ValueError("disposition must be CARRIER or EXCLUDE")
        if source not in _SOURCES:
            raise ValueError("source must be slack, public, github, or manual")
        if _REASON.fullmatch(reason_code) is None:
            raise ValueError("reason_code must be a short uppercase code")
        if pr_number is not None and (
            isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number <= 0
        ):
            raise ValueError("pr_number must be a positive integer")
        if disposition == "CARRIER" and pr_number is None:
            raise ValueError("CARRIER observations require pr_number")
        if head_sha is not None:
            head_sha = head_sha.strip().casefold()
            if _SHA.fullmatch(head_sha) is None:
                raise ValueError("head_sha must be a 40-64 character hexadecimal SHA")
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or not 60 <= ttl_seconds <= _MAX_TTL_SECONDS
        ):
            raise ValueError(f"ttl_seconds must be between 60 and {_MAX_TTL_SECONDS}")
        return disposition, source, reason_code, pr_number, head_sha, ttl_seconds

    def put(
        self,
        repo: str,
        issue: int,
        *,
        disposition: str,
        source: str,
        reason_code: str,
        pr_number: int | None = None,
        head_sha: str | None = None,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        observed_epoch: float | None = None,
    ) -> dict[str, Any]:
        repo = normalize_repo(repo)
        issue = normalize_issue(issue)
        disposition, source, reason_code, pr_number, head_sha, ttl_seconds = self._validated(
            disposition, source, reason_code, pr_number, head_sha, ttl_seconds
        )
        observed = time.time() if observed_epoch is None else observed_epoch
        if (
            isinstance(observed, bool)
            or not isinstance(observed, (int, float))
            or not math.isfinite(observed)
            or observed < 0
        ):
            raise ValueError("observed_epoch must be a finite non-negative number")
        observed = float(observed)
        expires = observed + ttl_seconds
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO github_intake_cache_v1("
                "repo, issue, disposition, pr_number, head_sha, source, reason_code, "
                "observed_epoch, expires_epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(repo, issue) DO UPDATE SET "
                "disposition=excluded.disposition, pr_number=excluded.pr_number, "
                "head_sha=excluded.head_sha, source=excluded.source, "
                "reason_code=excluded.reason_code, observed_epoch=excluded.observed_epoch, "
                "expires_epoch=excluded.expires_epoch "
                "WHERE excluded.observed_epoch >= github_intake_cache_v1.observed_epoch",
                (
                    repo, issue, disposition, pr_number, head_sha, source, reason_code,
                    observed, expires,
                ),
            )
        return self.lookup(repo, issue, now_epoch=observed) or {
            "repo": repo,
            "issue": issue,
            "hit": False,
            "suppress_authenticated_enumeration": False,
        }

    def lookup(
        self, repo: str, issue: int, *, now_epoch: float | None = None
    ) -> dict[str, Any] | None:
        repo = normalize_repo(repo)
        issue = normalize_issue(issue)
        now = time.time() if now_epoch is None else now_epoch
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(now)
            or now < 0
        ):
            raise ValueError("now_epoch must be a finite non-negative number")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT disposition, pr_number, head_sha, source, reason_code, "
                "observed_epoch, expires_epoch FROM github_intake_cache_v1 "
                "WHERE repo = ? AND issue = ?",
                (repo, issue),
            ).fetchone()
            if row is None:
                return None
            disposition, pr_number, head_sha, source, reason_code, observed, expires = row
            if (
                disposition not in _DISPOSITIONS
                or source not in _SOURCES
                or not isinstance(reason_code, str)
                or _REASON.fullmatch(reason_code) is None
                or not isinstance(observed, (int, float))
                or not math.isfinite(observed)
                or not isinstance(expires, (int, float))
                or not math.isfinite(expires)
            ):
                raise IntakeCacheError("shared GitHub intake cache record invalid")
            if float(expires) <= float(now):
                connection.execute(
                    "DELETE FROM github_intake_cache_v1 "
                    "WHERE repo = ? AND issue = ? AND expires_epoch = ?",
                    (repo, issue, expires),
                )
                return None
            return {
                "repo": repo,
                "issue": issue,
                "hit": True,
                "disposition": disposition,
                "pr_number": pr_number,
                "head_sha": head_sha,
                "source": source,
                "reason_code": reason_code,
                "observed_epoch": float(observed),
                "expires_epoch": float(expires),
                "suppress_authenticated_enumeration": True,
            }

    def clear(self, repo: str, issue: int) -> bool:
        repo = normalize_repo(repo)
        issue = normalize_issue(issue)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM github_intake_cache_v1 WHERE repo = ? AND issue = ?",
                (repo, issue),
            )
            return cursor.rowcount > 0

    def prune_expired(self, *, now_epoch: float | None = None) -> int:
        now = time.time() if now_epoch is None else now_epoch
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(now)
            or now < 0
        ):
            raise ValueError("now_epoch must be a finite non-negative number")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM github_intake_cache_v1 WHERE expires_epoch <= ?",
                (float(now),),
            )
            return cursor.rowcount


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cheap-first cache for repeated GitHub bounty intake observations."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def target(command: str) -> argparse.ArgumentParser:
        child = sub.add_parser(command)
        child.add_argument("repo")
        child.add_argument("issue", type=int)
        child.add_argument(
            "--cache-file",
            default=os.environ.get("CONCIERGE_INTAKE_CACHE"),
            help="shared SQLite path (or CONCIERGE_INTAKE_CACHE)",
        )
        return child

    put = target("put")
    put.add_argument("--disposition", choices=("carrier", "exclude"), required=True)
    put.add_argument("--source", choices=sorted(_SOURCES), required=True)
    put.add_argument("--reason", required=True)
    put.add_argument("--pr", type=int)
    put.add_argument("--head")
    put.add_argument("--ttl", type=int, default=_DEFAULT_TTL_SECONDS)
    target("get")
    target("clear")
    prune = sub.add_parser("prune")
    prune.add_argument(
        "--cache-file",
        default=os.environ.get("CONCIERGE_INTAKE_CACHE"),
        help="shared SQLite path (or CONCIERGE_INTAKE_CACHE)",
    )
    classify = sub.add_parser("classify-403")
    classify.add_argument("--message", required=True)
    classify.add_argument("--remaining", type=int)
    classify.add_argument("--retry-after", type=int)
    return parser


def _cache(args: argparse.Namespace, parser: argparse.ArgumentParser) -> GitHubIntakeCache:
    path = getattr(args, "cache_file", None)
    if not isinstance(path, str) or not path:
        parser.error("--cache-file or CONCIERGE_INTAKE_CACHE is required")
    return GitHubIntakeCache(path)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "classify-403":
            print(json.dumps({
                "classification": classify_github_403(
                    args.message,
                    remaining=args.remaining,
                    retry_after_seconds=args.retry_after,
                )
            }, sort_keys=True))
            return 0

        cache = _cache(args, parser)
        if args.command == "put":
            result = cache.put(
                args.repo, args.issue,
                disposition=args.disposition,
                source=args.source,
                reason_code=args.reason,
                pr_number=args.pr,
                head_sha=args.head,
                ttl_seconds=args.ttl,
            )
        elif args.command == "get":
            result = cache.lookup(args.repo, args.issue)
            if result is None:
                result = {
                    "repo": normalize_repo(args.repo),
                    "issue": normalize_issue(args.issue),
                    "hit": False,
                    "suppress_authenticated_enumeration": False,
                }
        elif args.command == "clear":
            result = {
                "repo": normalize_repo(args.repo),
                "issue": normalize_issue(args.issue),
                "cleared": cache.clear(args.repo, args.issue),
            }
        elif args.command == "prune":
            result = {"pruned": cache.prune_expired()}
        else:
            raise AssertionError("unreachable command")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, IntakeCacheError) as exc:
        print(json.dumps({
            "error": "intake_cache_unavailable",
            "detail": str(exc),
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
