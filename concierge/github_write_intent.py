# SPDX-License-Identifier: MIT
"""Persist head-fenced GitHub PR metadata writes across provider cooldowns.

This module performs no provider I/O.  It gives coordinated publishers a small,
durable handoff surface so a body-only PR update can survive a hot GitHub rail
without being reconstructed from Slack prose or replayed by multiple workers.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
from time import time
from typing import Any, Iterator, TextIO


_SCHEMA = "github-write-intent/v1"
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_HEAD_SHA = re.compile(r"[0-9a-f]{40}\Z")
_MAX_BODY_BYTES = 128 * 1024
_MAX_OPERATION_ID_BYTES = 160
_MAX_OWNER_BYTES = 128
_MAX_ERROR_CODE_BYTES = 96
_DEFAULT_LEASE_SECONDS = 60.0


class WriteIntentError(RuntimeError):
    """The durable write-intent store or requested state transition is invalid."""


@dataclass(frozen=True)
class WriteIntent:
    operation_id: str
    repository: str
    pull_number: int
    expected_head: str
    body: str
    payload_sha256: str
    state: str
    created_epoch: float
    not_before_epoch: float
    lease_owner: str | None
    lease_until_epoch: float | None
    last_error_code: str | None

    def public_record(self, *, include_body: bool = True) -> dict[str, Any]:
        record = asdict(self)
        record["schema"] = _SCHEMA
        if not include_body:
            record.pop("body", None)
        return record


def _finite_epoch(value: float | int | None, name: str) -> float:
    result = time() if value is None else float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return result


def _label(value: str, name: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or "\0" in value
    ):
        raise ValueError(f"{name} must be a non-empty label up to {maximum} bytes")
    return value


def _repository(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("repository must be owner/name")
    value = value.strip()
    if _REPOSITORY.fullmatch(value) is None:
        raise ValueError("repository must be owner/name")
    if any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("repository must be owner/name")
    return value


def _head(value: str, name: str = "expected_head") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a 40-character lowercase Git SHA")
    value = value.strip().lower()
    if _HEAD_SHA.fullmatch(value) is None:
        raise ValueError(f"{name} must be a 40-character lowercase Git SHA")
    return value


def _body(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("body must be UTF-8 text")
    encoded = value.encode("utf-8")
    if len(encoded) > _MAX_BODY_BYTES or "\0" in value:
        raise ValueError("body must be at most 128 KiB and contain no NUL")
    return value


def _payload_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class GitHubWriteIntentLedger:
    """SQLite-backed, single-claimer ledger for PR-body update intents."""

    def __init__(self, path: str | Path) -> None:
        if str(path) == ":memory:":
            raise ValueError("write-intent ledger must persist between processes")
        self.path = Path(path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
            connection = sqlite3.connect(self.path, timeout=1.0)
            connection.row_factory = sqlite3.Row
            connection.execute(
                "CREATE TABLE IF NOT EXISTS github_write_intent_v1 ("
                "operation_id TEXT PRIMARY KEY, "
                "repository TEXT NOT NULL, "
                "pull_number INTEGER NOT NULL, "
                "expected_head TEXT NOT NULL, "
                "body TEXT NOT NULL, "
                "payload_sha256 TEXT NOT NULL, "
                "state TEXT NOT NULL, "
                "created_epoch REAL NOT NULL, "
                "not_before_epoch REAL NOT NULL, "
                "lease_owner TEXT, "
                "lease_until_epoch REAL, "
                "last_error_code TEXT)"
            )
            connection.commit()
            with connection:
                yield connection
        except (OSError, sqlite3.Error, ValueError, OverflowError) as exc:
            if isinstance(exc, WriteIntentError):
                raise
            raise WriteIntentError("GitHub write-intent state unavailable") from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _decode(row: sqlite3.Row) -> WriteIntent:
        intent = WriteIntent(
            operation_id=row["operation_id"],
            repository=row["repository"],
            pull_number=row["pull_number"],
            expected_head=row["expected_head"],
            body=row["body"],
            payload_sha256=row["payload_sha256"],
            state=row["state"],
            created_epoch=row["created_epoch"],
            not_before_epoch=row["not_before_epoch"],
            lease_owner=row["lease_owner"],
            lease_until_epoch=row["lease_until_epoch"],
            last_error_code=row["last_error_code"],
        )
        if intent.state not in {"PENDING", "LEASED", "DONE"}:
            raise WriteIntentError("GitHub write-intent state invalid")
        if _payload_sha256(intent.body) != intent.payload_sha256:
            raise WriteIntentError("GitHub write-intent payload checksum mismatch")
        _repository(intent.repository)
        _head(intent.expected_head)
        if type(intent.pull_number) is not int or intent.pull_number <= 0:
            raise WriteIntentError("GitHub write-intent pull number invalid")
        _finite_epoch(intent.created_epoch, "created_epoch")
        _finite_epoch(intent.not_before_epoch, "not_before_epoch")
        if intent.lease_until_epoch is not None:
            _finite_epoch(intent.lease_until_epoch, "lease_until_epoch")
        return intent

    def enqueue_pr_body(
        self,
        *,
        operation_id: str,
        repository: str,
        pull_number: int,
        expected_head: str,
        body: str,
        now_epoch: float | None = None,
    ) -> WriteIntent:
        """Insert one immutable expected-head/body intent, idempotently."""
        operation_id = _label(
            operation_id, "operation_id", _MAX_OPERATION_ID_BYTES
        )
        repository = _repository(repository)
        if type(pull_number) is not int or pull_number <= 0:
            raise ValueError("pull_number must be a positive integer")
        expected_head = _head(expected_head)
        body = _body(body)
        now = _finite_epoch(now_epoch, "now_epoch")
        payload_sha256 = _payload_sha256(body)

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM github_write_intent_v1 WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is not None:
                existing = self._decode(row)
                identity = (
                    existing.repository,
                    existing.pull_number,
                    existing.expected_head,
                    existing.payload_sha256,
                )
                requested = (repository, pull_number, expected_head, payload_sha256)
                if identity != requested:
                    raise WriteIntentError(
                        "operation_id already belongs to a different write intent"
                    )
                return existing

            # A provider PR-body update has one live destination, even when
            # two fleet seats chose different operation IDs. Hold the SQLite
            # write lock while checking that target to prevent concurrent
            # enqueues from creating competing publisher leases.
            active = connection.execute(
                "SELECT * FROM github_write_intent_v1 "
                "WHERE lower(repository) = lower(?) AND pull_number = ? "
                "AND expected_head = ? AND state IN ('PENDING', 'LEASED') "
                "ORDER BY created_epoch, operation_id LIMIT 1",
                (repository, pull_number, expected_head),
            ).fetchone()
            if active is not None:
                canonical = self._decode(active)
                if canonical.payload_sha256 != payload_sha256:
                    raise WriteIntentError(
                        "active PR-head write intent has a different body; "
                        "reconcile the existing operation before publishing"
                    )
                return canonical

            connection.execute(
                "INSERT INTO github_write_intent_v1("
                "operation_id, repository, pull_number, expected_head, body, "
                "payload_sha256, state, created_epoch, not_before_epoch, "
                "lease_owner, lease_until_epoch, last_error_code"
                ") VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, NULL, NULL, NULL)",
                (
                    operation_id,
                    repository,
                    pull_number,
                    expected_head,
                    body,
                    payload_sha256,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM github_write_intent_v1 WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            assert row is not None
            return self._decode(row)

    def claim_ready(
        self,
        owner: str,
        *,
        now_epoch: float | None = None,
        lease_seconds: float = _DEFAULT_LEASE_SECONDS,
    ) -> WriteIntent | None:
        """Claim one due intent; an expired lease may be reclaimed by another worker."""
        owner = _label(owner, "owner", _MAX_OWNER_BYTES)
        now = _finite_epoch(now_epoch, "now_epoch")
        if (
            not isinstance(lease_seconds, (int, float))
            or not math.isfinite(float(lease_seconds))
            or not 1 <= float(lease_seconds) <= 15 * 60
        ):
            raise ValueError("lease_seconds must be between 1 and 900 seconds")
        lease_until = now + float(lease_seconds)

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            owned = connection.execute(
                "SELECT * FROM github_write_intent_v1 "
                "WHERE state = 'LEASED' AND lease_owner = ? "
                "AND lease_until_epoch > ? "
                "ORDER BY created_epoch, operation_id LIMIT 1",
                (owner, now),
            ).fetchone()
            if owned is not None:
                return self._decode(owned)

            row = connection.execute(
                "SELECT * FROM github_write_intent_v1 "
                "WHERE not_before_epoch <= ? AND ("
                "state = 'PENDING' OR "
                "(state = 'LEASED' AND lease_until_epoch <= ?)"
                ") ORDER BY created_epoch, operation_id LIMIT 1",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            operation_id = row["operation_id"]
            connection.execute(
                "UPDATE github_write_intent_v1 SET state = 'LEASED', "
                "lease_owner = ?, lease_until_epoch = ?, last_error_code = NULL "
                "WHERE operation_id = ?",
                (owner, lease_until, operation_id),
            )
            claimed = connection.execute(
                "SELECT * FROM github_write_intent_v1 WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            assert claimed is not None
            return self._decode(claimed)

    def defer(
        self,
        operation_id: str,
        owner: str,
        *,
        not_before_epoch: float,
        error_code: str,
    ) -> WriteIntent:
        """Return an owned intent to pending state with a provider-derived retry floor."""
        operation_id = _label(
            operation_id, "operation_id", _MAX_OPERATION_ID_BYTES
        )
        owner = _label(owner, "owner", _MAX_OWNER_BYTES)
        not_before = _finite_epoch(not_before_epoch, "not_before_epoch")
        error_code = _label(error_code, "error_code", _MAX_ERROR_CODE_BYTES)

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM github_write_intent_v1 WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise WriteIntentError("write intent does not exist")
            current = self._decode(row)
            if current.state != "LEASED" or current.lease_owner != owner:
                raise WriteIntentError("write intent is not leased by this owner")
            connection.execute(
                "UPDATE github_write_intent_v1 SET state = 'PENDING', "
                "not_before_epoch = MAX(not_before_epoch, ?), "
                "lease_owner = NULL, lease_until_epoch = NULL, "
                "last_error_code = ? WHERE operation_id = ?",
                (not_before, error_code, operation_id),
            )
            updated = connection.execute(
                "SELECT * FROM github_write_intent_v1 WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            assert updated is not None
            return self._decode(updated)

    def complete(
        self,
        operation_id: str,
        owner: str,
        *,
        observed_head: str,
    ) -> WriteIntent:
        """Mark an owned write DONE only if provider readback still matches its fence."""
        operation_id = _label(
            operation_id, "operation_id", _MAX_OPERATION_ID_BYTES
        )
        owner = _label(owner, "owner", _MAX_OWNER_BYTES)
        observed_head = _head(observed_head, "observed_head")

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM github_write_intent_v1 WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise WriteIntentError("write intent does not exist")
            current = self._decode(row)
            if current.state != "LEASED" or current.lease_owner != owner:
                raise WriteIntentError("write intent is not leased by this owner")
            if current.expected_head != observed_head:
                raise WriteIntentError(
                    "provider head drifted; leave the intent leased for explicit reconciliation"
                )
            connection.execute(
                "UPDATE github_write_intent_v1 SET state = 'DONE', "
                "lease_owner = NULL, lease_until_epoch = NULL, "
                "last_error_code = NULL WHERE operation_id = ?",
                (operation_id,),
            )
            updated = connection.execute(
                "SELECT * FROM github_write_intent_v1 WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            assert updated is not None
            return self._decode(updated)

    def records(self, *, limit: int = 100) -> list[WriteIntent]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM github_write_intent_v1 "
                "ORDER BY created_epoch, operation_id LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._decode(row) for row in rows]


def _read_body(path: str, stdin: TextIO) -> str:
    if path == "-":
        raw = stdin.read(_MAX_BODY_BYTES + 1)
    else:
        source = Path(path)
        if source.stat().st_size > _MAX_BODY_BYTES:
            raise ValueError("body exceeds 128 KiB")
        raw = source.read_text(encoding="utf-8")
    return _body(raw)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Persist/replay expected-head GitHub PR-body intents without provider I/O."
    )
    parser.add_argument(
        "--ledger",
        default=os.environ.get("CONCIERGE_GITHUB_WRITE_INTENTS"),
        help="Persistent SQLite path. Contents/body text are never printed unless claimed.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    enqueue = subparsers.add_parser("enqueue")
    enqueue.add_argument("--operation-id", required=True)
    enqueue.add_argument("--repository", required=True)
    enqueue.add_argument("--pull-number", required=True, type=int)
    enqueue.add_argument("--expected-head", required=True)
    enqueue.add_argument("--body-file", required=True)

    claim = subparsers.add_parser("claim")
    claim.add_argument("--owner", required=True)
    claim.add_argument("--lease-seconds", type=float, default=_DEFAULT_LEASE_SECONDS)

    defer = subparsers.add_parser("defer")
    defer.add_argument("--operation-id", required=True)
    defer.add_argument("--owner", required=True)
    defer.add_argument("--not-before-epoch", required=True, type=float)
    defer.add_argument("--error-code", required=True)

    complete = subparsers.add_parser("complete")
    complete.add_argument("--operation-id", required=True)
    complete.add_argument("--owner", required=True)
    complete.add_argument("--observed-head", required=True)

    listing = subparsers.add_parser("list")
    listing.add_argument("--limit", type=int, default=100)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.ledger:
        parser.error("--ledger or CONCIERGE_GITHUB_WRITE_INTENTS is required")

    try:
        ledger = GitHubWriteIntentLedger(args.ledger)
        if args.command == "enqueue":
            intent = ledger.enqueue_pr_body(
                operation_id=args.operation_id,
                repository=args.repository,
                pull_number=args.pull_number,
                expected_head=args.expected_head,
                body=_read_body(args.body_file, sys.stdin),
            )
            result: Any = intent.public_record(include_body=False)
        elif args.command == "claim":
            intent = ledger.claim_ready(
                args.owner, lease_seconds=args.lease_seconds
            )
            result = None if intent is None else intent.public_record(include_body=True)
        elif args.command == "defer":
            result = ledger.defer(
                args.operation_id,
                args.owner,
                not_before_epoch=args.not_before_epoch,
                error_code=args.error_code,
            ).public_record(include_body=False)
        elif args.command == "complete":
            result = ledger.complete(
                args.operation_id,
                args.owner,
                observed_head=args.observed_head,
            ).public_record(include_body=False)
        else:
            result = [
                intent.public_record(include_body=False)
                for intent in ledger.records(limit=args.limit)
            ]
    except (OSError, UnicodeError, ValueError, WriteIntentError) as exc:
        print(f"github-write-intent: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
