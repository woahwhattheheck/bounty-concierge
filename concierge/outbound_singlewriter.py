# SPDX-License-Identifier: MIT
"""Fail-closed single-writer guard for revenue-bearing outbound mutations.

Two independent fences are intentional. ``OutboundSingleWriter`` prevents two
processes sharing a state directory from sending the same operation. The shared
claim election lets separate cloud seats elect exactly one writer from a
complete durable coordination snapshot. This module never sends anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

SCHEMA = "outbound-singlewriter/v1"
MAX_TEXT = 1024
MAX_TTL = 86400


class OutboundGuardError(RuntimeError):
    pass


class InvalidIdentity(OutboundGuardError):
    pass


class LeaseBusy(OutboundGuardError):
    pass


class LeaseLost(OutboundGuardError):
    pass


class AlreadySent(OutboundGuardError):
    pass


class AmbiguousSend(OutboundGuardError):
    pass


class CorruptState(OutboundGuardError):
    pass


class SharedClaimError(OutboundGuardError):
    pass


class SharedClaimBlocked(OutboundGuardError):
    pass


@dataclass(frozen=True)
class OutboundIdentity:
    provider: str
    destination: str
    thread: str
    operation: str

    @property
    def canonical(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "destination": self.destination,
            "thread": self.thread,
            "operation": self.operation,
        }

    @property
    def key(self) -> str:
        return hashlib.sha256(_json(self.canonical)).hexdigest()


@dataclass(frozen=True)
class SharedClaimDecision:
    operation_key: str
    authorized: bool
    disposition: str
    winner_owner: Optional[str] = None
    winner_event_id: Optional[str] = None
    terminal_sent_owner: Optional[str] = None
    terminal_sent_event_id: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _text(name: str, value: Any, *, fold: bool = False, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise InvalidIdentity(f"{name} must be a string")
    value = unicodedata.normalize("NFC", value).strip()
    if not value or len(value) > limit:
        raise InvalidIdentity(f"{name} is empty or too long")
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        raise InvalidIdentity(f"{name} contains control/format characters")
    return value.casefold() if fold else value


def normalize_identity(*, provider: str, destination: str, thread: str,
                       operation: str) -> OutboundIdentity:
    """Normalize only syntax, never guess provider-specific address semantics."""
    return OutboundIdentity(
        _text("provider", provider, fold=True, limit=128),
        _text("destination", destination),
        _text("thread", thread),
        _text("operation", operation),
    )


def outbound_operation_key(*, provider: str, destination: str, thread: str,
                           operation: str) -> str:
    return normalize_identity(provider=provider, destination=destination,
                              thread=thread, operation=operation).key


def _now(value: Optional[datetime]) -> datetime:
    value = value or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OutboundGuardError("time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CorruptState("invalid UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as exc:
        raise CorruptState("invalid UTC timestamp") from exc


def _token(name: str, value: Any) -> str:
    try:
        return _text(name, value, limit=256)
    except InvalidIdentity as exc:
        raise OutboundGuardError(str(exc)) from exc


def _seal(record: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(record)
    out.pop("record_sha256", None)
    out["record_sha256"] = hashlib.sha256(_json(out)).hexdigest()
    return out


def _validate(record: Any, expected_key: Optional[str] = None) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise CorruptState("state must be an object")
    digest = record.get("record_sha256")
    unsigned = dict(record)
    unsigned.pop("record_sha256", None)
    if not isinstance(digest, str) or digest != hashlib.sha256(_json(unsigned)).hexdigest():
        raise CorruptState("state integrity mismatch")
    if record.get("schema") != SCHEMA:
        raise CorruptState("unsupported state schema")
    ident = record.get("identity")
    if not isinstance(ident, dict) or set(ident) != {"provider", "destination", "thread", "operation"}:
        raise CorruptState("malformed identity")
    try:
        identity = normalize_identity(**ident)
    except (InvalidIdentity, TypeError) as exc:
        raise CorruptState("invalid identity") from exc
    key = record.get("operation_key")
    if key != identity.key or (expected_key is not None and key != expected_key):
        raise CorruptState("operation key mismatch")
    if record.get("status") not in {"HELD", "SENDING", "SENT", "RELEASED"}:
        raise CorruptState("invalid status")
    if not isinstance(record.get("generation"), int) or isinstance(record["generation"], bool) or record["generation"] < 1:
        raise CorruptState("invalid generation")
    if not isinstance(record.get("owner"), str) or not record["owner"]:
        raise CorruptState("invalid owner")
    for field in ("created_at", "updated_at"):
        _time(record.get(field))
    if record["status"] == "HELD":
        _time(record.get("expires_at"))
        if not record.get("lease_id") or record.get("permit_id") is not None:
            raise CorruptState("invalid HELD record")
    elif record["status"] == "SENDING":
        if record.get("expires_at") is not None or not record.get("permit_id"):
            raise CorruptState("invalid SENDING record")
    elif record["status"] == "SENT":
        if not record.get("provider_receipt_id") or not record.get("permit_id"):
            raise CorruptState("invalid SENT record")
    return dict(record)


def _lock_fd(fd: int) -> None:
    if os.name == "nt":  # pragma: no cover - Windows runner path
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock_fd(fd: int) -> None:
    if os.name == "nt":  # pragma: no cover - Windows runner path
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


class OutboundSingleWriter:
    """Durable local state machine for one outbound operation."""

    def __init__(self, root: Union[str, os.PathLike[str]]) -> None:
        self.root = Path(root)
        self.states = self.root / "state"
        self.locks = self.root / "locks"
        self.states.mkdir(parents=True, exist_ok=True)
        self.locks.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise OutboundGuardError("operation_key must be lowercase SHA-256 hex")
        return self.states / f"{key}.json"

    @contextmanager
    def _locked(self, key: str):
        self._path(key)
        path = self.locks / f"{key}.lock"
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(str(path), flags, 0o600)
        try:
            _lock_fd(fd)
            yield
        finally:
            _unlock_fd(fd)
            os.close(fd)

    def _read(self, key: str) -> Optional[dict[str, Any]]:
        path = self._path(key)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        if len(raw) > 131072:
            raise CorruptState("state file is too large")
        try:
            return _validate(json.loads(raw.decode("utf-8")), key)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CorruptState("state JSON is malformed") from exc

    def _write(self, key: str, record: Mapping[str, Any]) -> dict[str, Any]:
        record = _validate(_seal(record), key)
        data = _json(record) + b"\n"
        fd, name = tempfile.mkstemp(prefix=f".{key}.", dir=str(self.states))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.close(fd)
            fd = -1
            os.replace(name, self._path(key))
            if hasattr(os, "O_DIRECTORY"):
                dfd = os.open(str(self.states), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass
        return record

    def acquire(self, *, provider: str, destination: str, thread: str,
                operation: str, owner: str, ttl_seconds: int = 300,
                now: Optional[datetime] = None) -> dict[str, Any]:
        identity = normalize_identity(provider=provider, destination=destination,
                                      thread=thread, operation=operation)
        owner = _token("owner", owner)
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= MAX_TTL:
            raise OutboundGuardError("ttl_seconds must be an integer from 1 to 86400")
        at = _now(now)
        with self._locked(identity.key):
            current = self._read(identity.key)
            generation = 1
            created_at = _stamp(at)
            if current:
                generation = current["generation"] + 1
                created_at = current["created_at"]
                if current["status"] == "SENT":
                    raise AlreadySent("operation already has a successful-send receipt")
                if current["status"] == "SENDING":
                    raise AmbiguousSend("prior sender outcome is ambiguous; reconcile before retry")
                if current["status"] == "HELD" and _time(current["expires_at"]) > at:
                    raise LeaseBusy("operation has a live local lease")
            lease = uuid.uuid4().hex
            return self._write(identity.key, {
                "schema": SCHEMA, "operation_key": identity.key,
                "identity": identity.canonical, "status": "HELD", "owner": owner,
                "generation": generation, "lease_id": lease, "permit_id": None,
                "provider_receipt_id": None, "evidence_reference": None,
                "created_at": created_at, "updated_at": _stamp(at),
                "expires_at": _stamp(at + timedelta(seconds=ttl_seconds)),
            })

    def prepare_send(self, *, operation_key: str, lease_id: str, owner: str,
                     now: Optional[datetime] = None) -> dict[str, Any]:
        owner, lease_id, at = _token("owner", owner), _token("lease_id", lease_id), _now(now)
        with self._locked(operation_key):
            current = self._read(operation_key)
            if not current or current["status"] != "HELD" or current["owner"] != owner or current["lease_id"] != lease_id:
                raise LeaseLost("lease no longer owns operation")
            if _time(current["expires_at"]) <= at:
                raise LeaseLost("lease expired before send preparation")
            current.update(status="SENDING", lease_id=None, permit_id=uuid.uuid4().hex,
                           expires_at=None, updated_at=_stamp(at))
            return self._write(operation_key, current)

    def finalize_sent(self, *, operation_key: str, permit_id: str, owner: str,
                      provider_receipt_id: str,
                      now: Optional[datetime] = None) -> dict[str, Any]:
        owner, permit_id = _token("owner", owner), _token("permit_id", permit_id)
        receipt, at = _token("provider_receipt_id", provider_receipt_id), _now(now)
        with self._locked(operation_key):
            current = self._read(operation_key)
            if current and current["status"] == "SENT":
                if current["owner"] == owner and current["permit_id"] == permit_id and current["provider_receipt_id"] == receipt:
                    return current
                raise AlreadySent("operation already finalized with another receipt")
            if not current or current["status"] != "SENDING" or current["owner"] != owner or current["permit_id"] != permit_id:
                raise LeaseLost("send permit no longer owns operation")
            current.update(status="SENT", provider_receipt_id=receipt, updated_at=_stamp(at))
            return self._write(operation_key, current)

    def abort_held(self, *, operation_key: str, lease_id: str, owner: str,
                   now: Optional[datetime] = None) -> dict[str, Any]:
        owner, lease_id, at = _token("owner", owner), _token("lease_id", lease_id), _now(now)
        with self._locked(operation_key):
            current = self._read(operation_key)
            if not current or current["status"] != "HELD" or current["owner"] != owner or current["lease_id"] != lease_id:
                raise LeaseLost("only the current HELD owner may release")
            current.update(status="RELEASED", lease_id=None, expires_at=None, updated_at=_stamp(at))
            return self._write(operation_key, current)

    def reconcile_not_sent(self, *, operation_key: str, permit_id: str, owner: str,
                           evidence_reference: str,
                           now: Optional[datetime] = None) -> dict[str, Any]:
        owner, permit_id = _token("owner", owner), _token("permit_id", permit_id)
        evidence, at = _text("evidence_reference", evidence_reference), _now(now)
        with self._locked(operation_key):
            current = self._read(operation_key)
            if not current or current["status"] != "SENDING" or current["owner"] != owner or current["permit_id"] != permit_id:
                raise LeaseLost("only the current ambiguous sender may reconcile")
            current.update(status="RELEASED", permit_id=None, evidence_reference=evidence,
                           updated_at=_stamp(at))
            return self._write(operation_key, current)

    def inspect(self, operation_key: str) -> Optional[dict[str, Any]]:
        with self._locked(operation_key):
            return self._read(operation_key)


def _order(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise SharedClaimError("shared event order must be numeric")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise SharedClaimError("shared event order is malformed") from exc
    if not parsed.is_finite():
        raise SharedClaimError("shared event order must be finite")
    return parsed


def evaluate_shared_claim(*, operation_key: str, owner: str, claim_event_id: str,
                          events: Iterable[Mapping[str, Any]],
                          snapshot_complete: bool) -> SharedClaimDecision:
    """Elect the earliest active shared claim from a complete post-ack snapshot.

    Shared claims intentionally have no timeout. A crash after the external send
    may look identical to a stale claim; time alone can never authorize retry.
    """
    key, owner, own_id = _token("operation_key", operation_key), _token("owner", owner), _token("claim_event_id", claim_event_id)
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise SharedClaimError("operation_key must be lowercase SHA-256 hex")
    if snapshot_complete is not True:
        raise SharedClaimBlocked("shared snapshot is incomplete/unavailable; do not send")
    rows = []
    seen = set()
    for raw in events:
        if not isinstance(raw, Mapping) or raw.get("operation_key") != key:
            raise SharedClaimError("shared event has wrong operation key")
        kind = raw.get("kind")
        if kind not in {"CLAIM", "RELEASE", "SENT"}:
            raise SharedClaimError("invalid shared event kind")
        event_owner, event_id = _token("owner", raw.get("owner")), _token("event_id", raw.get("event_id"))
        if event_id in seen:
            raise SharedClaimError("duplicate shared event_id")
        seen.add(event_id)
        rows.append((_order(raw.get("order")), event_id, kind, event_owner))
    if own_id not in seen:
        raise SharedClaimBlocked("own acknowledged claim is absent from post-ack snapshot")
    rows.sort(key=lambda row: (row[0], row[1]))
    active = {}
    terminal = None
    for order, event_id, kind, event_owner in rows:
        if terminal is not None:
            if kind == "SENT":
                raise SharedClaimError("multiple terminal SENT events")
            continue
        if kind == "CLAIM":
            if event_owner in active:
                raise SharedClaimError("duplicate active claim by one owner")
            active[event_owner] = (order, event_id)
        elif kind == "RELEASE":
            if event_owner not in active:
                raise SharedClaimError("RELEASE without active claim")
            del active[event_owner]
        else:
            if event_owner not in active:
                raise SharedClaimError("SENT without active claim")
            terminal = (event_owner, event_id)
            active.clear()
    if terminal:
        return SharedClaimDecision(key, False, "ALREADY_SENT",
                                   terminal_sent_owner=terminal[0], terminal_sent_event_id=terminal[1])
    if not active:
        return SharedClaimDecision(key, False, "NO_ACTIVE_CLAIM")
    winner, (_, winner_id) = min(active.items(), key=lambda item: (item[1][0], item[1][1], item[0]))
    authorized = winner == owner and active.get(owner, (None, None))[1] == own_id
    return SharedClaimDecision(key, authorized, "AUTHORIZED" if authorized else "LOST_ELECTION",
                               winner_owner=winner, winner_event_id=winner_id)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=".outbound-singlewriter")
    sub = p.add_subparsers(dest="command", required=True)
    key = sub.add_parser("key")
    for name in ("provider", "destination", "thread", "operation"):
        key.add_argument(f"--{name}", required=True)
    status = sub.add_parser("status"); status.add_argument("--key", required=True)
    elect = sub.add_parser("elect")
    elect.add_argument("--key", required=True); elect.add_argument("--owner", required=True)
    elect.add_argument("--claim-event-id", required=True); elect.add_argument("--events-json", required=True)
    elect.add_argument("--snapshot-complete", action="store_true")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "key":
            out = {"operation_key": outbound_operation_key(provider=args.provider,
                    destination=args.destination, thread=args.thread, operation=args.operation)}
        elif args.command == "status":
            out = OutboundSingleWriter(args.root).inspect(args.key) or {"status": "ABSENT", "operation_key": args.key}
        else:
            try:
                events = json.loads(Path(args.events_json).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SharedClaimError("cannot load events JSON") from exc
            if not isinstance(events, list):
                raise SharedClaimError("events JSON must contain a list")
            decision = evaluate_shared_claim(operation_key=args.key, owner=args.owner,
                claim_event_id=args.claim_event_id, events=events,
                snapshot_complete=args.snapshot_complete)
            out = decision.as_dict()
            print(json.dumps(out, sort_keys=True, separators=(",", ":")))
            return 0 if decision.authorized else 3
        print(json.dumps(out, sort_keys=True, separators=(",", ":")))
        return 0
    except OutboundGuardError as exc:
        print(json.dumps({"error": exc.__class__.__name__, "message": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
