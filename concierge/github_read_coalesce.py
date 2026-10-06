# SPDX-License-Identifier: MIT
"""Cross-process short-TTL coalescing for read-only GitHub fences."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
from threading import Lock
from time import time
from typing import Any, Callable, Optional

_DEFAULT_TTL_SECONDS = 3.0
_DEFAULT_LEASE_SECONDS = 5.0
_MAX_PAYLOAD_BYTES = 256 * 1024


class ReadCoalesceStateError(RuntimeError):
    """Shared read-coalescing state is unavailable or inconsistent."""


@dataclass(frozen=True)
class ReadAdmission:
    status: str
    provider_called: bool
    payload: Optional[Any]
    observed_epoch: Optional[float]
    expires_epoch: Optional[float]
    retry_after_seconds: int
    generation: int
    advisory_only: bool = True


class GitHubReadCoalescer:
    """Deduplicate identical read-only GitHub fences for a few seconds.

    scope is an already-public rail/account label, not a secret. Both scope
    and query identity are hashed before persistence. Cached receipts are
    advisory only; write paths still need their own live expected-head checks.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        scope: str,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        lease_seconds: float = _DEFAULT_LEASE_SECONDS,
    ) -> None:
        if str(path) == ":memory:":
            raise ValueError("read coalescer must persist between processes")
        self.scope_key = self._hash_label("scope", scope, 256)
        if not math.isfinite(ttl_seconds) or not 0 < ttl_seconds <= 30:
            raise ValueError("ttl_seconds must be between 0 and 30 seconds")
        if not math.isfinite(lease_seconds) or not 0 < lease_seconds <= 30:
            raise ValueError("lease_seconds must be between 0 and 30 seconds")
        self.path = Path(path)
        self.ttl_seconds = float(ttl_seconds)
        self.lease_seconds = float(lease_seconds)
        self._schema_ready = False
        self._schema_lock = Lock()

    @staticmethod
    def _hash_label(kind: str, value: str, limit: int) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value.encode("utf-8")) > limit
            or "\0" in value
        ):
            raise ValueError(f"{kind} must be a non-empty value up to {limit} bytes")
        return hashlib.sha256(
            b"github-read-coalesce-v1\0"
            + kind.encode("ascii")
            + b"\0"
            + value.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _now(value: Optional[float]) -> float:
        current = time() if value is None else float(value)
        if not math.isfinite(current) or current < 0:
            raise ValueError("now_epoch must be finite and non-negative")
        return current

    def _query_key(self, query_identity: str) -> str:
        return self._hash_label("query", query_identity, 1024)

    def _connect(self) -> sqlite3.Connection:
        try:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
            connection = sqlite3.connect(self.path, timeout=1.0)
            if not self._schema_ready:
                with self._schema_lock:
                    if not self._schema_ready:
                        connection.execute(
                            "CREATE TABLE IF NOT EXISTS github_read_coalesce_v1 ("
                            "scope_key TEXT NOT NULL, query_key TEXT NOT NULL, "
                            "payload_json TEXT, observed_epoch REAL, expires_epoch REAL, "
                            "lease_owner TEXT, lease_until_epoch REAL, generation INTEGER NOT NULL, "
                            "PRIMARY KEY(scope_key, query_key))"
                        )
                        connection.commit()
                        self._schema_ready = True
            return connection
        except (OSError, sqlite3.Error, ValueError, OverflowError) as exc:
            raise ReadCoalesceStateError("shared GitHub read state unavailable") from exc

    @staticmethod
    def _payload(raw: Optional[str]) -> Optional[Any]:
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ReadCoalesceStateError("shared GitHub read payload invalid") from exc

    def admit(
        self,
        query_identity: str,
        owner: str,
        *,
        now_epoch: Optional[float] = None,
    ) -> ReadAdmission:
        query_key = self._query_key(query_identity)
        owner_key = self._hash_label("owner", owner, 256)
        now = self._now(now_epoch)
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT payload_json, observed_epoch, expires_epoch, "
                    "lease_owner, lease_until_epoch, generation "
                    "FROM github_read_coalesce_v1 WHERE scope_key=? AND query_key=?",
                    (self.scope_key, query_key),
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO github_read_coalesce_v1("
                        "scope_key, query_key, payload_json, observed_epoch, expires_epoch, "
                        "lease_owner, lease_until_epoch, generation"
                        ") VALUES (?, ?, NULL, NULL, NULL, ?, ?, 1)",
                        (self.scope_key, query_key, owner_key, now + self.lease_seconds),
                    )
                    return ReadAdmission("FRESH", False, None, None, None, 0, 1)

                payload_raw, observed, expires, lease_owner, lease_until, generation = row
                generation = int(generation)
                if expires is not None and float(expires) > now and payload_raw is not None:
                    return ReadAdmission(
                        "CACHED",
                        False,
                        self._payload(payload_raw),
                        float(observed),
                        float(expires),
                        0,
                        generation,
                    )
                if lease_until is not None and float(lease_until) > now:
                    return ReadAdmission(
                        "IN_FLIGHT",
                        False,
                        None,
                        float(observed) if observed is not None else None,
                        float(expires) if expires is not None else None,
                        max(1, math.ceil(float(lease_until) - now)),
                        generation,
                    )

                next_generation = generation + 1
                cursor = connection.execute(
                    "UPDATE github_read_coalesce_v1 "
                    "SET lease_owner=?, lease_until_epoch=?, generation=? "
                    "WHERE scope_key=? AND query_key=? AND generation=?",
                    (
                        owner_key,
                        now + self.lease_seconds,
                        next_generation,
                        self.scope_key,
                        query_key,
                        generation,
                    ),
                )
                if cursor.rowcount != 1:
                    return ReadAdmission("IN_FLIGHT", False, None, None, None, 1, generation)
                return ReadAdmission(
                    "STALE" if payload_raw is not None else "FRESH",
                    False,
                    None,
                    float(observed) if observed is not None else None,
                    float(expires) if expires is not None else None,
                    0,
                    next_generation,
                )
        except (sqlite3.Error, ValueError, OverflowError) as exc:
            raise ReadCoalesceStateError("shared GitHub read state unavailable") from exc
        finally:
            connection.close()

    def publish(
        self,
        query_identity: str,
        owner: str,
        generation: int,
        payload: Any,
        *,
        now_epoch: Optional[float] = None,
    ) -> bool:
        query_key = self._query_key(query_identity)
        owner_key = self._hash_label("owner", owner, 256)
        now = self._now(now_epoch)
        if not isinstance(generation, int) or generation <= 0:
            raise ValueError("generation must be positive")
        try:
            encoded = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("payload must be finite JSON") from exc
        if len(encoded.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise ValueError("payload exceeds 256 KiB")

        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    "UPDATE github_read_coalesce_v1 "
                    "SET payload_json=?, observed_epoch=?, expires_epoch=?, "
                    "lease_owner=NULL, lease_until_epoch=NULL "
                    "WHERE scope_key=? AND query_key=? AND generation=? AND lease_owner=?",
                    (
                        encoded,
                        now,
                        now + self.ttl_seconds,
                        self.scope_key,
                        query_key,
                        generation,
                        owner_key,
                    ),
                )
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise ReadCoalesceStateError("shared GitHub read state unavailable") from exc
        finally:
            connection.close()

    def release(self, query_identity: str, owner: str, generation: int) -> bool:
        query_key = self._query_key(query_identity)
        owner_key = self._hash_label("owner", owner, 256)
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "UPDATE github_read_coalesce_v1 "
                    "SET lease_owner=NULL, lease_until_epoch=NULL "
                    "WHERE scope_key=? AND query_key=? AND generation=? AND lease_owner=?",
                    (self.scope_key, query_key, generation, owner_key),
                )
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise ReadCoalesceStateError("shared GitHub read state unavailable") from exc
        finally:
            connection.close()


def coalesced_read(
    cache: GitHubReadCoalescer,
    query_identity: str,
    owner: str,
    reader: Callable[[], Any],
    *,
    now_epoch: Optional[float] = None,
) -> ReadAdmission:
    admission = cache.admit(query_identity, owner, now_epoch=now_epoch)
    if admission.status in ("CACHED", "IN_FLIGHT"):
        return admission
    try:
        payload = reader()
    except Exception:
        cache.release(query_identity, owner, admission.generation)
        raise
    if not cache.publish(
        query_identity,
        owner,
        admission.generation,
        payload,
        now_epoch=now_epoch,
    ):
        raise ReadCoalesceStateError("read refresh lease no longer owned")
    observed = GitHubReadCoalescer._now(now_epoch)
    return ReadAdmission(
        admission.status,
        True,
        payload,
        observed,
        observed + cache.ttl_seconds,
        0,
        admission.generation,
    )
