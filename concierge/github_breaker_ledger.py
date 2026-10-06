# SPDX-License-Identifier: MIT
"""Cross-process GitHub route breaker admission with no provider I/O."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import sqlite3
from threading import Lock
from time import time
from typing import Iterator
from contextlib import contextmanager


class BreakerStateError(RuntimeError):
    """Shared breaker state could not be used safely."""


CLOSED = "CLOSED"
OPEN_UNTIL = "OPEN_UNTIL"
SCOPE_DENIED = "SCOPE_DENIED"
AUTH_FAILED = "AUTH_FAILED"
_ALLOWED_STATES = {CLOSED, OPEN_UNTIL, SCOPE_DENIED, AUTH_FAILED}
_ALLOWED_OPS = {"search", "read-known-coordinate", "write", "fork"}
_RATE_ERRORS = {"PRIMARY_RATE_LIMIT", "SECONDARY_RATE_LIMIT"}
_TERMINAL_ERRORS = {"SCOPE_DENIED": SCOPE_DENIED, "AUTH_FAILED": AUTH_FAILED}
_DEFAULT_UNKNOWN_WAIT = 60.0
_DEFAULT_LEASE_SECONDS = 15.0
_DEFAULT_STALE_TTL = 6.0 * 60.0 * 60.0


@dataclass(frozen=True)
class Admission:
    decision: str
    state: str
    reason: str | None
    until_epoch: float | None
    retry_after_seconds: int


class GitHubBreakerLedger:
    """Share GitHub circuit state by credential/provider route and operation class.

    The route label and credential material are hashed before persistence. This
    helper never performs provider I/O. Callers feed normalized provider receipts
    into :meth:`record_receipt`, then call :meth:`admit` immediately before a
    GitHub transport call.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        provider_route: str,
        credential: str | None = None,
        stale_ttl_seconds: float = _DEFAULT_STALE_TTL,
    ) -> None:
        if str(path) == ":memory:":
            raise ValueError("breaker file must persist between processes")
        if (
            not isinstance(provider_route, str)
            or not provider_route
            or len(provider_route.encode("utf-8")) > 128
            or "\0" in provider_route
        ):
            raise ValueError("provider_route must be a non-empty label up to 128 bytes")
        if not math.isfinite(stale_ttl_seconds) or stale_ttl_seconds <= 0:
            raise ValueError("stale_ttl_seconds must be positive")
        self.path = Path(path)
        self.route_key = hashlib.sha256(
            b"github-breaker-route-v1\0"
            + provider_route.encode("utf-8")
            + b"\0"
            + (credential or "").encode("utf-8")
        ).hexdigest()
        self.stale_ttl_seconds = float(stale_ttl_seconds)
        self._schema_ready = False
        self._schema_lock = Lock()

    def _validate_operation(self, operation_class: str) -> None:
        if operation_class not in _ALLOWED_OPS:
            raise ValueError("unsupported GitHub breaker operation class")

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            connection.execute(
                "CREATE TABLE IF NOT EXISTS github_breaker_v1 ("
                "route_key TEXT NOT NULL, operation_class TEXT NOT NULL, "
                "state TEXT NOT NULL, reason TEXT, until_epoch REAL, "
                "observed_epoch REAL NOT NULL, generation INTEGER NOT NULL, "
                "lease_owner TEXT, lease_until_epoch REAL, lease_generation INTEGER, "
                "PRIMARY KEY(route_key, operation_class))"
            )
            connection.commit()
            self._schema_ready = True

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
            self._ensure_schema(connection)
            with connection:
                yield connection
        except (OSError, sqlite3.Error, ValueError, OverflowError) as exc:
            raise BreakerStateError("shared GitHub breaker state unavailable") from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _wait_deadline(
        observed_epoch: float,
        retry_after_seconds: float | None,
        reset_epoch: float | None,
    ) -> float:
        candidates = []
        if retry_after_seconds is not None:
            if not math.isfinite(retry_after_seconds) or retry_after_seconds < 0:
                raise ValueError("retry_after_seconds must be finite and non-negative")
            candidates.append(observed_epoch + float(retry_after_seconds))
        if reset_epoch is not None:
            if not math.isfinite(reset_epoch) or reset_epoch < 0:
                raise ValueError("reset_epoch must be finite and non-negative")
            candidates.append(float(reset_epoch))
        if not candidates:
            candidates.append(observed_epoch + _DEFAULT_UNKNOWN_WAIT)
        return max(observed_epoch, max(candidates))

    def record_receipt(
        self,
        operation_class: str,
        error_class: str,
        *,
        observed_epoch: float | None = None,
        retry_after_seconds: float | None = None,
        reset_epoch: float | None = None,
    ) -> None:
        """Ingest one normalized provider receipt without storing raw payloads."""
        self._validate_operation(operation_class)
        observed = time() if observed_epoch is None else float(observed_epoch)
        if not math.isfinite(observed) or observed < 0:
            raise ValueError("observed_epoch must be finite and non-negative")
        if error_class not in _RATE_ERRORS and error_class not in _TERMINAL_ERRORS:
            raise ValueError("unsupported GitHub breaker error class")

        if error_class in _RATE_ERRORS:
            state = OPEN_UNTIL
            deadline = self._wait_deadline(observed, retry_after_seconds, reset_epoch)
        else:
            state = _TERMINAL_ERRORS[error_class]
            deadline = None

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, until_epoch, generation FROM github_breaker_v1 "
                "WHERE route_key = ? AND operation_class = ?",
                (self.route_key, operation_class),
            ).fetchone()
            generation = 1
            if row is not None:
                previous_state, previous_deadline, previous_generation = row
                generation = int(previous_generation) + 1
                if previous_state in (SCOPE_DENIED, AUTH_FAILED) and state == OPEN_UNTIL:
                    state = previous_state
                    deadline = None
                elif state == OPEN_UNTIL and previous_state == OPEN_UNTIL:
                    if previous_deadline is not None:
                        deadline = max(float(previous_deadline), float(deadline))
            connection.execute(
                "INSERT INTO github_breaker_v1("
                "route_key, operation_class, state, reason, until_epoch, observed_epoch, generation, "
                "lease_owner, lease_until_epoch, lease_generation) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL) "
                "ON CONFLICT(route_key, operation_class) DO UPDATE SET "
                "state=excluded.state, reason=excluded.reason, until_epoch=excluded.until_epoch, "
                "observed_epoch=excluded.observed_epoch, generation=excluded.generation, "
                "lease_owner=NULL, lease_until_epoch=NULL, lease_generation=NULL",
                (
                    self.route_key,
                    operation_class,
                    state,
                    error_class,
                    deadline,
                    observed,
                    generation,
                ),
            )

    def admit(
        self,
        operation_class: str,
        *,
        owner: str,
        now_epoch: float | None = None,
        lease_seconds: float = _DEFAULT_LEASE_SECONDS,
    ) -> Admission:
        """Return ALLOW, SKIP, or PROBE before transport; never performs I/O."""
        self._validate_operation(operation_class)
        if not owner or len(owner.encode("utf-8")) > 128 or "\0" in owner:
            raise ValueError("owner must be a non-empty value up to 128 bytes")
        now = time() if now_epoch is None else float(now_epoch)
        if not math.isfinite(now) or now < 0:
            raise ValueError("now_epoch must be finite and non-negative")
        if not math.isfinite(lease_seconds) or lease_seconds <= 0 or lease_seconds > 120:
            raise ValueError("lease_seconds must be between 0 and 120 seconds")

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, reason, until_epoch, observed_epoch, generation, "
                "lease_owner, lease_until_epoch, lease_generation "
                "FROM github_breaker_v1 WHERE route_key = ? AND operation_class = ?",
                (self.route_key, operation_class),
            ).fetchone()
            if row is None:
                return Admission("ALLOW", CLOSED, None, None, 0)

            state, reason, deadline, observed, generation, lease_owner, lease_until, lease_generation = row
            if state not in _ALLOWED_STATES:
                raise BreakerStateError("shared GitHub breaker state invalid")
            if state in (SCOPE_DENIED, AUTH_FAILED):
                return Admission("SKIP", state, reason, None, 0)
            if state == CLOSED:
                return Admission("ALLOW", CLOSED, reason, None, 0)
            if deadline is None or not math.isfinite(deadline) or deadline < 0:
                raise BreakerStateError("shared GitHub breaker deadline invalid")
            deadline = float(deadline)
            if deadline > now:
                return Admission(
                    "SKIP", OPEN_UNTIL, reason, deadline, max(0, math.ceil(deadline - now))
                )

            if lease_until is not None and float(lease_until) > now:
                if lease_owner == owner and lease_generation == generation:
                    return Admission("PROBE", OPEN_UNTIL, reason, float(lease_until), 0)
                return Admission(
                    "SKIP", OPEN_UNTIL, "RECOVERY_PROBE_IN_FLIGHT", float(lease_until),
                    max(0, math.ceil(float(lease_until) - now)),
                )

            new_lease_until = now + float(lease_seconds)
            cursor = connection.execute(
                "UPDATE github_breaker_v1 SET lease_owner=?, lease_until_epoch=?, lease_generation=? "
                "WHERE route_key=? AND operation_class=? AND generation=?",
                (
                    owner,
                    new_lease_until,
                    generation,
                    self.route_key,
                    operation_class,
                    generation,
                ),
            )
            if cursor.rowcount != 1:
                return Admission("SKIP", OPEN_UNTIL, "RECOVERY_RACE", None, 1)
            return Admission("PROBE", OPEN_UNTIL, reason, new_lease_until, 0)

    def complete_probe(
        self,
        operation_class: str,
        *,
        owner: str,
        success: bool,
        now_epoch: float | None = None,
    ) -> bool:
        """CAS-close only the generation protected by this successful probe."""
        self._validate_operation(operation_class)
        now = time() if now_epoch is None else float(now_epoch)
        if not math.isfinite(now) or now < 0:
            raise ValueError("now_epoch must be finite and non-negative")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT generation, lease_owner, lease_until_epoch, lease_generation, until_epoch "
                "FROM github_breaker_v1 WHERE route_key=? AND operation_class=?",
                (self.route_key, operation_class),
            ).fetchone()
            if row is None or row[1] != owner or row[3] != row[0]:
                return False
            generation = int(row[0])
            lease_until = row[2]
            if (
                lease_until is None
                or not math.isfinite(float(lease_until))
                or float(lease_until) < 0
            ):
                raise BreakerStateError("shared GitHub breaker lease invalid")
            if success:
                cursor = connection.execute(
                    "UPDATE github_breaker_v1 SET state=?, reason=NULL, until_epoch=NULL, "
                    "observed_epoch=?, generation=?, lease_owner=NULL, lease_until_epoch=NULL, "
                    "lease_generation=NULL WHERE route_key=? AND operation_class=? AND generation=?",
                    (
                        CLOSED,
                        now,
                        generation + 1,
                        self.route_key,
                        operation_class,
                        generation,
                    ),
                )
                return cursor.rowcount == 1
            previous_deadline = row[4]
            if previous_deadline is not None:
                if (
                    not math.isfinite(float(previous_deadline))
                    or float(previous_deadline) < 0
                ):
                    raise BreakerStateError("shared GitHub breaker deadline invalid")
                retry_until = max(now, float(lease_until), float(previous_deadline))
            else:
                retry_until = max(now, float(lease_until))
            cursor = connection.execute(
                "UPDATE github_breaker_v1 SET state=?, until_epoch=?, observed_epoch=?, "
                "generation=?, lease_owner=NULL, lease_until_epoch=NULL, lease_generation=NULL "
                "WHERE route_key=? AND operation_class=? AND generation=?",
                (
                    OPEN_UNTIL,
                    retry_until,
                    now,
                    generation + 1,
                    self.route_key,
                    operation_class,
                    generation,
                ),
            )
            return cursor.rowcount == 1

    def cleanup_stale(self, *, now_epoch: float | None = None) -> int:
        """Delete stale rows and expired leases so dead workers cannot accumulate state."""
        now = time() if now_epoch is None else float(now_epoch)
        if not math.isfinite(now) or now < 0:
            raise ValueError("now_epoch must be finite and non-negative")
        cutoff = now - self.stale_ttl_seconds
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE github_breaker_v1 SET lease_owner=NULL, lease_until_epoch=NULL, "
                "lease_generation=NULL WHERE route_key=? AND lease_until_epoch IS NOT NULL "
                "AND lease_until_epoch <= ?",
                (self.route_key, now),
            )
            cursor = connection.execute(
                "DELETE FROM github_breaker_v1 WHERE route_key=? AND observed_epoch < ? "
                "AND (lease_until_epoch IS NULL OR lease_until_epoch <= ?)",
                (self.route_key, cutoff, now),
            )
            return cursor.rowcount
