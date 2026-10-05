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


_UNKNOWN_BACKOFF_BASE_SECONDS = 60.0
_UNKNOWN_BACKOFF_MAX_SECONDS = 15.0 * 60.0
_UNKNOWN_BACKOFF_RESET_SECONDS = 30.0 * 60.0


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
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS github_quota_reserve_v1 "
                    "(scope TEXT PRIMARY KEY, until_epoch REAL NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS github_cooldown_unknown_v1 "
                    "(scope TEXT PRIMARY KEY, backoff_seconds REAL NOT NULL, "
                    "observed_epoch REAL NOT NULL)"
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

    def quota_reserve_deadline(self) -> float | None:
        """Return the shared primary-quota reservation deadline, if any."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT until_epoch FROM github_quota_reserve_v1 WHERE scope = ?",
                (self.scope,),
            ).fetchone()
            if row is None:
                return None
            value = row[0]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise CooldownStateError("shared GitHub quota reservation deadline invalid")
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

    def reserve_quota_until(self, until_epoch: float) -> None:
        """Share a quota-headroom deadline without recording a provider throttle."""
        if not math.isfinite(until_epoch) or until_epoch < 0:
            raise CooldownStateError("shared GitHub quota reservation deadline invalid")
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO github_quota_reserve_v1(scope, until_epoch) VALUES (?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET until_epoch = "
                "MAX(github_quota_reserve_v1.until_epoch, excluded.until_epoch)",
                (self.scope, until_epoch),
            )

    def extend_unknown_secondary(self) -> float:
        """Escalate repeated secondary throttles that expose no provider deadline.

        GitHub recommends waiting at least one minute when a secondary-limit
        response omits Retry-After, then increasing the wait if the limit
        persists. Store only the bounded delay and observation time; no request
        payload, endpoint or credential is retained. Return the effective shared
        deadline, including any later deadline already recorded by another worker.
        """
        now = time()
        with self._connection() as connection:
            # Serialize the read/advance/write so simultaneous workers cannot
            # all publish the same fallback step after one limiter burst.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT backoff_seconds, observed_epoch "
                "FROM github_cooldown_unknown_v1 WHERE scope = ?",
                (self.scope,),
            ).fetchone()
            backoff = _UNKNOWN_BACKOFF_BASE_SECONDS
            if row is not None:
                previous, observed = row
                if (
                    not isinstance(previous, (int, float))
                    or not math.isfinite(previous)
                    or previous < _UNKNOWN_BACKOFF_BASE_SECONDS
                    or previous > _UNKNOWN_BACKOFF_MAX_SECONDS
                    or not isinstance(observed, (int, float))
                    or not math.isfinite(observed)
                    or observed < 0
                ):
                    raise CooldownStateError("shared GitHub cooldown backoff invalid")
                age = now - float(observed)
                if 0 <= age <= _UNKNOWN_BACKOFF_RESET_SECONDS:
                    # Concurrent in-flight responses from one limiter burst do
                    # not count as retries. Escalate only after the previous
                    # wait has actually elapsed; otherwise retain that step.
                    if age < float(previous):
                        backoff = float(previous)
                    else:
                        backoff = min(
                            _UNKNOWN_BACKOFF_MAX_SECONDS,
                            max(_UNKNOWN_BACKOFF_BASE_SECONDS, float(previous) * 2.0),
                        )
            until_epoch = now + backoff
            connection.execute(
                "INSERT INTO github_cooldown_unknown_v1"
                "(scope, backoff_seconds, observed_epoch) VALUES (?, ?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET "
                "backoff_seconds = excluded.backoff_seconds, "
                "observed_epoch = excluded.observed_epoch",
                (self.scope, backoff, now),
            )
            connection.execute(
                "INSERT INTO github_cooldown_v1(scope, until_epoch) VALUES (?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET until_epoch = "
                "MAX(github_cooldown_v1.until_epoch, excluded.until_epoch)",
                (self.scope, until_epoch),
            )
            # Read MAX back under the same lock: an in-flight response may
            # arrive after another worker recorded a later provider deadline.
            row = connection.execute(
                "SELECT until_epoch FROM github_cooldown_v1 WHERE scope = ?",
                (self.scope,),
            ).fetchone()
            value = row[0]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise CooldownStateError("shared GitHub cooldown deadline invalid")
            return float(value)


def cooldown_deadline(
    *, retry_seconds: int | None, retry_at: str | None,
    reset_at: int | None, primary_exhausted: bool,
) -> float:
    """Honor provider deadlines, or use a 60-second unknown-interval fallback.

    A reset header for a non-exhausted primary quota does not describe the
    secondary throttle. Ignore that unrelated reset rather than pausing an hour.
    An explicit zero delay or elapsed deadline is known, not a missing hint;
    do not add a new fallback minute after the provider wait has ended.
    """
    now = time()
    deadlines: list[float] = []
    if retry_seconds is not None:
        deadlines.append(now + retry_seconds)
    if retry_at is not None:
        deadlines.append(datetime.fromisoformat(retry_at.replace("Z", "+00:00")).timestamp())
    if primary_exhausted and reset_at is not None:
        deadlines.append(float(reset_at))
    known = [value for value in deadlines if math.isfinite(value)]
    return max(now, max(known)) if known else now + 60.0
