# SPDX-License-Identifier: MIT
"""Opt-in, cross-process quota deadlines; no HTTP, credentials or response cache."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import math
import os
from pathlib import Path
import sqlite3
from time import time
from typing import Iterator


class CooldownStateError(RuntimeError):
    """A requested shared store could not be used; do not bypass it."""


class GitHubCooldown:
    """Share only a deadline between workers using one file and credential.

    The parent directory must already exist and be private to the operator.
    SQLite transactions extend deadlines atomically. No database lock is held
    during HTTP. Already in-flight requests cannot be recalled by this helper.
    """

    def __init__(self, path: str | Path, token: str | None) -> None:
        if str(path) == ":memory:":
            raise ValueError("cooldown file must persist between processes")
        self.path = Path(path)
        self.scope = hashlib.sha256(
            b"github-rest-cooldown-v1\0" + (token or "").encode("utf-8")
        ).hexdigest()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            # Do not change a process-wide umask or rewrite an existing file.
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
            connection = sqlite3.connect(self.path, timeout=1.0)
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS github_cooldown_v1 "
                    "(scope TEXT PRIMARY KEY, until_epoch REAL NOT NULL)"
                )
                yield connection
        except (OSError, sqlite3.Error, ValueError, OverflowError) as exc:
            # Paths and database contents must not escape into capture reports.
            raise CooldownStateError("shared GitHub cooldown state unavailable") from exc
        finally:
            if connection is not None:
                connection.close()

    def deadline(self) -> float | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT until_epoch FROM github_cooldown_v1 WHERE scope = ?",
                (self.scope,),
            ).fetchone()
            if row is None:
                return None
            value = row[0]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise CooldownStateError("shared GitHub cooldown deadline invalid")
            return float(value)

    def extend(self, until_epoch: float) -> None:
        if not math.isfinite(until_epoch) or until_epoch < 0:
            raise CooldownStateError("shared GitHub cooldown deadline invalid")
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO github_cooldown_v1(scope, until_epoch) VALUES (?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET until_epoch = "
                "MAX(github_cooldown_v1.until_epoch, excluded.until_epoch)",
                (self.scope, until_epoch),
            )


def cooldown_deadline(
    *, retry_seconds: int | None, retry_at: str | None,
    reset_at: int | None, primary_exhausted: bool,
) -> float:
    """Honor provider deadlines, or use a 60-second unknown-interval fallback.

    A reset header for a non-exhausted primary quota does not describe the
    secondary throttle. Ignore that unrelated reset rather than pausing an hour.
    """
    now = time()
    deadlines: list[float] = []
    if retry_seconds is not None:
        deadlines.append(now + retry_seconds)
    if retry_at is not None:
        deadlines.append(datetime.fromisoformat(retry_at.replace("Z", "+00:00")).timestamp())
    if primary_exhausted and reset_at is not None:
        deadlines.append(float(reset_at))
    future = [value for value in deadlines if math.isfinite(value) and value > now]
    return max(future) if future else now + 60.0
