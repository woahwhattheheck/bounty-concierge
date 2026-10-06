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
from threading import Lock
from time import time
from typing import Iterator


class CooldownStateError(RuntimeError):
    """A requested shared store could not be used; do not bypass it."""


_UNKNOWN_BACKOFF_BASE_SECONDS = 60.0
_UNKNOWN_BACKOFF_MAX_SECONDS = 15.0 * 60.0
_UNKNOWN_BACKOFF_RESET_SECONDS = 30.0 * 60.0
_RECOVERY_LEASE_SECONDS = 15.0


class GitHubCooldown:
    """Share quota and provider deadlines between coordinated workers.

    Provider cooldowns are credential-global by default for backward
    compatibility. Callers may opt into a hashed route-family scope while
    primary-quota reservations remain credential-global.

    The parent directory must already exist and be private to the operator.
    SQLite transactions extend deadlines atomically. No database lock is held
    during HTTP. Already in-flight requests cannot be recalled by this helper.
    """

    def __init__(
        self,
        path: str | Path,
        token: str | None,
        *,
        cooldown_scope: str | None = None,
    ) -> None:
        if str(path) == ":memory:":
            raise ValueError("cooldown file must persist between processes")
        if cooldown_scope is not None and (
            not cooldown_scope
            or len(cooldown_scope.encode("utf-8")) > 64
            or "\0" in cooldown_scope
        ):
            raise ValueError("cooldown_scope must be a non-empty label up to 64 bytes")
        self.path = Path(path)
        # Keep the v1 credential scope byte-for-byte for legacy callers and for
        # primary-quota reservations, which apply across GitHub REST resources.
        self.scope = hashlib.sha256(
            b"github-rest-cooldown-v1\0" + (token or "").encode("utf-8")
        ).hexdigest()
        # Provider secondary limits can be resource-family specific. Opted-in
        # callers get an isolated provider-cooldown row without exposing the
        # credential fingerprint or the human-readable route label in SQLite.
        self.cooldown_scope = self.scope if cooldown_scope is None else hashlib.sha256(
            b"github-rest-cooldown-scope-v1\0"
            + self.scope.encode("ascii")
            + b"\0"
            + cooldown_scope.encode("utf-8")
        ).hexdigest()
        self._schema_ready = False
        self._schema_lock = Lock()

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        """Initialize the shared schema once per helper instance."""
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
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
            connection.execute(
                "CREATE TABLE IF NOT EXISTS github_recovery_lease_v1 "
                "(scope TEXT PRIMARY KEY, owner TEXT NOT NULL, "
                "lease_until_epoch REAL NOT NULL, cooldown_epoch REAL NOT NULL)"
            )
            # Keep schema creation outside the caller's data transaction. If a
            # later operation fails, subsequent calls must still see the tables.
            connection.commit()
            self._schema_ready = True

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
            self._ensure_schema(connection)
            with connection:
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
                (self.cooldown_scope,),
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

    @contextmanager
    def _read_only_connection(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = sqlite3.connect(
                self.path.absolute().as_uri() + "?mode=ro",
                timeout=1.0,
                uri=True,
            )
            yield connection
        except (OSError, sqlite3.Error, ValueError, OverflowError) as exc:
            raise CooldownStateError("shared GitHub cooldown state unavailable") from exc
        finally:
            if connection is not None:
                connection.close()

    def deadlines(
        self, *, read_only: bool = False
    ) -> tuple[float | None, float | None]:
        """Read provider and quota-reservation deadlines in one SQLite snapshot.

        A single statement keeps the pair consistent and avoids opening and
        initializing the same store twice for each discovery preflight.
        Read-only observation never creates a database or initializes its schema;
        missing or incomplete state raises CooldownStateError.
        """
        connection_context = (
            self._read_only_connection() if read_only else self._connection()
        )
        with connection_context as connection:
            rows = connection.execute(
                "SELECT 'cooldown', until_epoch "
                "FROM github_cooldown_v1 WHERE scope = ? "
                "UNION ALL SELECT 'quota reservation', until_epoch "
                "FROM github_quota_reserve_v1 WHERE scope = ?",
                (self.cooldown_scope, self.scope),
            ).fetchall()
            values: dict[str, float] = {}
            for label, value in rows:
                if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise CooldownStateError(f"shared GitHub {label} deadline invalid")
                values[label] = float(value)
            return values.get("cooldown"), values.get("quota reservation")

    def extend(self, until_epoch: float) -> None:
        if not math.isfinite(until_epoch) or until_epoch < 0:
            raise CooldownStateError("shared GitHub cooldown deadline invalid")
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO github_cooldown_v1(scope, until_epoch) VALUES (?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET until_epoch = "
                "MAX(github_cooldown_v1.until_epoch, excluded.until_epoch)",
                (self.cooldown_scope, until_epoch),
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


    def claim_recovery_probe(
        self, owner: str, *, lease_seconds: float = _RECOVERY_LEASE_SECONDS,
    ) -> tuple[bool, bool, float | None]:
        """Claim the single recovery probe for an expired credential cooldown.

        Return (required, acquired, lease_until). Route-scoped secondary
        cooldowns deliberately bypass this credential-global lease.
        """
        if self.cooldown_scope != self.scope:
            return False, False, None
        if (
            not isinstance(owner, str)
            or not owner
            or len(owner.encode("utf-8")) > 128
            or "\0" in owner
        ):
            raise ValueError("recovery owner must be a non-empty value up to 128 bytes")
        if not math.isfinite(lease_seconds) or lease_seconds <= 0 or lease_seconds > 120:
            raise ValueError("recovery lease must be between 0 and 120 seconds")
        now = time()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT until_epoch FROM github_cooldown_v1 WHERE scope = ?",
                (self.scope,),
            ).fetchone()
            if row is None:
                return False, False, None
            deadline = row[0]
            if (
                not isinstance(deadline, (int, float))
                or not math.isfinite(deadline)
                or deadline < 0
            ):
                raise CooldownStateError("shared GitHub cooldown deadline invalid")
            deadline = float(deadline)
            if deadline > now:
                return False, False, deadline

            lease = connection.execute(
                "SELECT owner, lease_until_epoch, cooldown_epoch "
                "FROM github_recovery_lease_v1 WHERE scope = ?",
                (self.scope,),
            ).fetchone()
            if lease is not None:
                lease_owner, lease_until, lease_deadline = lease
                if (
                    not isinstance(lease_owner, str)
                    or not lease_owner
                    or not isinstance(lease_until, (int, float))
                    or not math.isfinite(lease_until)
                    or lease_until < 0
                    or not isinstance(lease_deadline, (int, float))
                    or not math.isfinite(lease_deadline)
                    or lease_deadline < 0
                ):
                    raise CooldownStateError("shared GitHub recovery lease invalid")
                if float(lease_until) > now and lease_owner != owner:
                    return True, False, float(lease_until)
                if float(lease_until) > now and lease_owner == owner:
                    return True, True, float(lease_until)

            lease_until = now + float(lease_seconds)
            connection.execute(
                "INSERT INTO github_recovery_lease_v1"
                "(scope, owner, lease_until_epoch, cooldown_epoch) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET owner = excluded.owner, "
                "lease_until_epoch = excluded.lease_until_epoch, "
                "cooldown_epoch = excluded.cooldown_epoch",
                (self.scope, owner, lease_until, deadline),
            )
            return True, True, lease_until

    def complete_recovery_probe(self, owner: str) -> bool:
        """Clear only the expired cooldown protected by this lease owner."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute(
                "SELECT owner, cooldown_epoch FROM github_recovery_lease_v1 "
                "WHERE scope = ?",
                (self.scope,),
            ).fetchone()
            if lease is None or lease[0] != owner:
                return False
            anchor = lease[1]
            if (
                not isinstance(anchor, (int, float))
                or not math.isfinite(anchor)
                or anchor < 0
            ):
                raise CooldownStateError("shared GitHub recovery lease invalid")
            cursor = connection.execute(
                "DELETE FROM github_cooldown_v1 "
                "WHERE scope = ? AND until_epoch <= ?",
                (self.scope, float(anchor)),
            )
            cleared = cursor.rowcount > 0
            if cleared:
                connection.execute(
                    "DELETE FROM github_cooldown_unknown_v1 WHERE scope = ?",
                    (self.scope,),
                )
            connection.execute(
                "DELETE FROM github_recovery_lease_v1 "
                "WHERE scope = ? AND owner = ?",
                (self.scope, owner),
            )
            return cleared

    def defer_recovery_probe(self, owner: str, until_epoch: float) -> bool:
        """Extend the cooldown and release a failed/throttled recovery probe."""
        if not math.isfinite(until_epoch) or until_epoch < 0:
            raise CooldownStateError("shared GitHub cooldown deadline invalid")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute(
                "SELECT owner FROM github_recovery_lease_v1 WHERE scope = ?",
                (self.scope,),
            ).fetchone()
            if lease is None or lease[0] != owner:
                return False
            connection.execute(
                "INSERT INTO github_cooldown_v1(scope, until_epoch) VALUES (?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET until_epoch = "
                "MAX(github_cooldown_v1.until_epoch, excluded.until_epoch)",
                (self.scope, until_epoch),
            )
            connection.execute(
                "DELETE FROM github_recovery_lease_v1 "
                "WHERE scope = ? AND owner = ?",
                (self.scope, owner),
            )
            return True

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
                (self.cooldown_scope,),
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
                (self.cooldown_scope, backoff, now),
            )
            connection.execute(
                "INSERT INTO github_cooldown_v1(scope, until_epoch) VALUES (?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET until_epoch = "
                "MAX(github_cooldown_v1.until_epoch, excluded.until_epoch)",
                (self.cooldown_scope, until_epoch),
            )
            # Read MAX back under the same lock: an in-flight response may
            # arrive after another worker recorded a later provider deadline.
            row = connection.execute(
                "SELECT until_epoch FROM github_cooldown_v1 WHERE scope = ?",
                (self.cooldown_scope,),
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
