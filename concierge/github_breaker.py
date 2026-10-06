# SPDX-License-Identifier: MIT
"""Shared GitHub circuit-breaker state for coordinated workers.

The ledger is transport-free: it records only a hashed route identity,
operation class, normalized error class, deadlines and recovery leases. It does
not persist tokens, request URLs, response bodies, headers, or provider payloads.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import sqlite3
from threading import Lock
from time import time
from typing import Iterator, Optional

OPERATION_CLASSES = frozenset({"search", "read-known-coordinate", "write", "fork"})
RATE_LIMIT_ERRORS = frozenset({"PRIMARY_RATE_LIMIT", "SECONDARY_RATE_LIMIT"})
TERMINAL_ERRORS = {
    "INTEGRATION_SCOPE_DENIED": "SCOPE_DENIED",
    "SCOPE_DENIED": "SCOPE_DENIED",
    "AUTH_FAILED": "AUTH_FAILED",
}
STATES = frozenset({"CLOSED", "OPEN_UNTIL", "SCOPE_DENIED", "AUTH_FAILED"})

_DEFAULT_RECOVERY_LEASE_SECONDS = 15.0
_DEFAULT_STALE_TTL_SECONDS = 24.0 * 60.0 * 60.0
_DEFAULT_DENIAL_TTL_SECONDS = 30.0 * 60.0
_DEFAULT_UNKNOWN_LIMIT_SECONDS = 60.0


class BreakerStateError(RuntimeError):
    """The shared breaker store is invalid or unavailable."""


@dataclass(frozen=True)
class Admission:
    """Network-free admission result for one route and operation class."""

    action: str
    state: str
    reason: Optional[str] = None
    deadline_epoch: Optional[float] = None
    lease_until_epoch: Optional[float] = None


class GitHubBreakerLedger:
    """Coordinate GitHub route admission across independent workers.

    ``route`` is an opaque caller label for one credential/provider path, for
    example ``app:installation-155467469`` or ``token:personal``. The label is
    hashed before persistence. ``operation_class`` deliberately separates broad
    searches from known-coordinate reads, writes and fork creation.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        stale_ttl_seconds: float = _DEFAULT_STALE_TTL_SECONDS,
        denial_ttl_seconds: float = _DEFAULT_DENIAL_TTL_SECONDS,
        recovery_lease_seconds: float = _DEFAULT_RECOVERY_LEASE_SECONDS,
    ) -> None:
        if str(path) == ":memory:":
            raise ValueError("breaker file must persist between processes")
        for value, label, upper in (
            (stale_ttl_seconds, "stale_ttl_seconds", 7 * 24 * 60 * 60),
            (denial_ttl_seconds, "denial_ttl_seconds", 24 * 60 * 60),
            (recovery_lease_seconds, "recovery_lease_seconds", 120),
        ):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 or value > upper:
                raise ValueError(f"{label} is outside the supported range")
        self.path = Path(path)
        self.stale_ttl_seconds = float(stale_ttl_seconds)
        self.denial_ttl_seconds = float(denial_ttl_seconds)
        self.recovery_lease_seconds = float(recovery_lease_seconds)
        self._schema_ready = False
        self._schema_lock = Lock()

    @staticmethod
    def _validate_route(route: str) -> None:
        if (
            not isinstance(route, str)
            or not route
            or len(route.encode("utf-8")) > 256
            or "\0" in route
        ):
            raise ValueError("route must be a non-empty label up to 256 bytes")

    @staticmethod
    def _validate_operation(operation_class: str) -> None:
        if operation_class not in OPERATION_CLASSES:
            raise ValueError(
                "operation_class must be search, read-known-coordinate, write, or fork"
            )

    @staticmethod
    def _validate_owner(owner: str) -> None:
        if (
            not isinstance(owner, str)
            or not owner
            or len(owner.encode("utf-8")) > 128
            or "\0" in owner
        ):
            raise ValueError("owner must be a non-empty value up to 128 bytes")

    @staticmethod
    def _finite(value: Optional[float], label: str) -> Optional[float]:
        if value is None:
            return None
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{label} must be a finite non-negative number")
        return float(value)

    def _key(self, route: str, operation_class: str) -> str:
        self._validate_route(route)
        self._validate_operation(operation_class)
        return hashlib.sha256(
            b"github-breaker-v1\0"
            + route.encode("utf-8")
            + b"\0"
            + operation_class.encode("ascii")
        ).hexdigest()

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            connection.execute(
                "CREATE TABLE IF NOT EXISTS github_breaker_v1 ("
                "key_hash TEXT PRIMARY KEY, "
                "operation_class TEXT NOT NULL, "
                "state TEXT NOT NULL, "
                "error_class TEXT NOT NULL, "
                "deadline_epoch REAL, "
                "observed_epoch REAL NOT NULL, "
                "expires_epoch REAL NOT NULL, "
                "generation INTEGER NOT NULL, "
                "lease_owner TEXT, "
                "lease_until_epoch REAL, "
                "lease_generation INTEGER)"
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

    def record(
        self,
        route: str,
        operation_class: str,
        *,
        error_class: str,
        observed_epoch: Optional[float] = None,
        reset_epoch: Optional[float] = None,
        retry_after_seconds: Optional[float] = None,
    ) -> Admission:
        """Record a normalized provider failure without storing provider payloads."""
        key = self._key(route, operation_class)
        observed = self._finite(
            time() if observed_epoch is None else observed_epoch, "observed_epoch"
        )
        assert observed is not None
        reset = self._finite(reset_epoch, "reset_epoch")
        retry = self._finite(retry_after_seconds, "retry_after_seconds")

        if error_class in RATE_LIMIT_ERRORS:
            state = "OPEN_UNTIL"
            deadlines = [
                value
                for value in (
                    reset,
                    observed + retry if retry is not None else None,
                )
                if value is not None
            ]
            deadline = max(deadlines) if deadlines else observed + _DEFAULT_UNKNOWN_LIMIT_SECONDS
            deadline = max(deadline, observed)
            expires = max(deadline, observed) + self.stale_ttl_seconds
        elif error_class in TERMINAL_ERRORS:
            state = TERMINAL_ERRORS[error_class]
            deadline = None
            expires = observed + self.denial_ttl_seconds
        else:
            raise ValueError("unsupported GitHub breaker error_class")

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, deadline_epoch, observed_epoch, expires_epoch, generation "
                "FROM github_breaker_v1 WHERE key_hash = ?",
                (key,),
            ).fetchone()
            generation = 1
            if row is not None:
                previous_state, previous_deadline, previous_observed, previous_expires, previous_generation = row
                if previous_state not in STATES - {"CLOSED"}:
                    raise BreakerStateError("shared GitHub breaker state invalid")
                values = (previous_observed, previous_expires, previous_generation)
                if (
                    not isinstance(values[0], (int, float))
                    or not math.isfinite(values[0])
                    or values[0] < 0
                    or not isinstance(values[1], (int, float))
                    or not math.isfinite(values[1])
                    or values[1] < 0
                    or type(values[2]) is not int
                    or values[2] < 1
                ):
                    raise BreakerStateError("shared GitHub breaker row invalid")
                generation = previous_generation + 1
                observed = max(observed, float(previous_observed))
                expires = max(expires, float(previous_expires))
                if state == "OPEN_UNTIL" and previous_state == "OPEN_UNTIL":
                    if (
                        not isinstance(previous_deadline, (int, float))
                        or not math.isfinite(previous_deadline)
                        or previous_deadline < 0
                    ):
                        raise BreakerStateError("shared GitHub breaker deadline invalid")
                    deadline = max(float(deadline), float(previous_deadline))

            connection.execute(
                "INSERT INTO github_breaker_v1("
                "key_hash, operation_class, state, error_class, deadline_epoch, "
                "observed_epoch, expires_epoch, generation, lease_owner, "
                "lease_until_epoch, lease_generation) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL) "
                "ON CONFLICT(key_hash) DO UPDATE SET "
                "operation_class=excluded.operation_class, state=excluded.state, "
                "error_class=excluded.error_class, deadline_epoch=excluded.deadline_epoch, "
                "observed_epoch=excluded.observed_epoch, expires_epoch=excluded.expires_epoch, "
                "generation=excluded.generation, lease_owner=NULL, "
                "lease_until_epoch=NULL, lease_generation=NULL",
                (
                    key,
                    operation_class,
                    state,
                    error_class,
                    deadline,
                    observed,
                    expires,
                    generation,
                ),
            )
        return Admission(
            action="SKIP",
            state=state,
            reason=error_class,
            deadline_epoch=deadline,
        )

    def admit(
        self,
        route: str,
        operation_class: str,
        *,
        owner: Optional[str] = None,
        now_epoch: Optional[float] = None,
    ) -> Admission:
        """Return ALLOW, SKIP or the single PROBE lease without network I/O."""
        key = self._key(route, operation_class)
        now = self._finite(time() if now_epoch is None else now_epoch, "now_epoch")
        assert now is not None
        if owner is not None:
            self._validate_owner(owner)

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, error_class, deadline_epoch, expires_epoch, generation, "
                "lease_owner, lease_until_epoch, lease_generation "
                "FROM github_breaker_v1 WHERE key_hash = ?",
                (key,),
            ).fetchone()
            if row is None:
                return Admission(action="ALLOW", state="CLOSED")

            (
                state,
                error_class,
                deadline,
                expires,
                generation,
                lease_owner,
                lease_until,
                lease_generation,
            ) = row
            if state not in STATES - {"CLOSED"} or not isinstance(error_class, str):
                raise BreakerStateError("shared GitHub breaker state invalid")
            if not isinstance(expires, (int, float)) or not math.isfinite(expires) or expires < 0:
                raise BreakerStateError("shared GitHub breaker expiry invalid")
            if float(expires) <= now:
                connection.execute(
                    "DELETE FROM github_breaker_v1 WHERE key_hash = ?",
                    (key,),
                )
                return Admission(action="ALLOW", state="CLOSED")

            if state in {"SCOPE_DENIED", "AUTH_FAILED"}:
                return Admission(
                    action="SKIP",
                    state=state,
                    reason=error_class,
                    deadline_epoch=float(expires),
                )

            if (
                not isinstance(deadline, (int, float))
                or not math.isfinite(deadline)
                or deadline < 0
            ):
                raise BreakerStateError("shared GitHub breaker deadline invalid")
            deadline = float(deadline)
            if deadline > now:
                return Admission(
                    action="SKIP",
                    state="OPEN_UNTIL",
                    reason=error_class,
                    deadline_epoch=deadline,
                )
            if owner is None:
                return Admission(
                    action="SKIP",
                    state="OPEN_UNTIL",
                    reason="RECOVERY_PROBE_REQUIRED",
                    deadline_epoch=deadline,
                )

            if lease_owner is not None:
                if (
                    not isinstance(lease_owner, str)
                    or not isinstance(lease_until, (int, float))
                    or not math.isfinite(lease_until)
                    or lease_until < 0
                    or type(lease_generation) is not int
                ):
                    raise BreakerStateError("shared GitHub recovery lease invalid")
                if float(lease_until) > now:
                    if lease_owner == owner:
                        return Admission(
                            action="PROBE",
                            state="OPEN_UNTIL",
                            reason=error_class,
                            deadline_epoch=deadline,
                            lease_until_epoch=float(lease_until),
                        )
                    return Admission(
                        action="SKIP",
                        state="OPEN_UNTIL",
                        reason="RECOVERY_PROBE_LEASED",
                        deadline_epoch=float(lease_until),
                        lease_until_epoch=float(lease_until),
                    )

            lease_until = now + self.recovery_lease_seconds
            connection.execute(
                "UPDATE github_breaker_v1 SET lease_owner=?, lease_until_epoch=?, "
                "lease_generation=? WHERE key_hash=?",
                (owner, lease_until, generation, key),
            )
            return Admission(
                action="PROBE",
                state="OPEN_UNTIL",
                reason=error_class,
                deadline_epoch=deadline,
                lease_until_epoch=lease_until,
            )

    def complete_probe(
        self,
        route: str,
        operation_class: str,
        *,
        owner: str,
        success: bool,
    ) -> bool:
        """Close an unchanged breaker after a successful owner-held probe.

        Fresh failures increment the row generation and win over an older probe.
        A failed probe only releases its lease; the caller should record the
        fresh normalized failure separately.
        """
        self._validate_owner(owner)
        key = self._key(route, operation_class)
        if type(success) is not bool:
            raise ValueError("success must be boolean")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, generation, lease_owner, lease_generation "
                "FROM github_breaker_v1 WHERE key_hash = ?",
                (key,),
            ).fetchone()
            if row is None:
                return False
            state, generation, lease_owner, lease_generation = row
            if lease_owner != owner:
                return False
            if success and state == "OPEN_UNTIL" and generation == lease_generation:
                connection.execute(
                    "DELETE FROM github_breaker_v1 WHERE key_hash = ?",
                    (key,),
                )
                return True
            connection.execute(
                "UPDATE github_breaker_v1 SET lease_owner=NULL, lease_until_epoch=NULL, "
                "lease_generation=NULL WHERE key_hash=? AND lease_owner=?",
                (key, owner),
            )
            return False

    def clear(self, route: str, operation_class: str) -> bool:
        """Explicitly clear a route after an independent healthy auth/scope probe."""
        key = self._key(route, operation_class)
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM github_breaker_v1 WHERE key_hash = ?",
                (key,),
            )
            return cursor.rowcount > 0

    def cleanup(self, *, now_epoch: Optional[float] = None) -> int:
        """Remove stale rows whose TTL and any recovery lease have both expired."""
        now = self._finite(time() if now_epoch is None else now_epoch, "now_epoch")
        assert now is not None
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM github_breaker_v1 "
                "WHERE expires_epoch <= ? AND "
                "(lease_until_epoch IS NULL OR lease_until_epoch <= ?)",
                (now, now),
            )
            return cursor.rowcount


__all__ = [
    "Admission",
    "BreakerStateError",
    "GitHubBreakerLedger",
    "OPERATION_CLASSES",
    "RATE_LIMIT_ERRORS",
    "STATES",
]
