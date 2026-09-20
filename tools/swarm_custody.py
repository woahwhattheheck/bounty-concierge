#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Deterministic, provider-neutral custody ledger for swarm work coordination.

Input is newline-delimited JSON (NDJSON), one event per line. The ledger performs
no network calls and derives custody solely from caller-supplied events plus an
explicit ``--as-of`` timestamp and lease duration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TextIO

SCHEMA_VERSION = 1
EVENT_KINDS = frozenset({"TAKE", "PROGRESS", "DONE", "RELEASE"})
DISPOSITIONS = ("OPEN", "ACTIVE", "COLLISION", "STALE", "DONE", "RELEASED")
_ALLOWED_FIELDS = frozenset(
    {"version", "event_id", "work_key", "kind", "owner", "at", "artifact"}
)
_REQUIRED_FIELDS = frozenset(
    {"version", "event_id", "work_key", "kind", "owner", "at"}
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}$")


class LedgerError(ValueError):
    """A stable, machine-readable custody-ledger error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Event:
    version: int
    event_id: str
    work_key: str
    kind: str
    owner: str
    at: datetime
    artifact: str | None

    def normalized(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": self.version,
            "event_id": self.event_id,
            "work_key": self.work_key,
            "kind": self.kind,
            "owner": self.owner,
            "at": _format_time(self.at),
        }
        if self.artifact is not None:
            payload["artifact"] = self.artifact
        return payload


def _format_time(value: datetime) -> str:
    value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_time(raw: Any, *, field: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise LedgerError("invalid_timestamp", f"{field} must be a non-empty string")
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise LedgerError("invalid_timestamp", f"{field} is not valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LedgerError("invalid_timestamp", f"{field} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _identifier(raw: Any, *, field: str, max_len: int = 200) -> str:
    if not isinstance(raw, str):
        raise LedgerError("invalid_identifier", f"{field} must be a string")
    if len(raw) > max_len or _ID_RE.fullmatch(raw) is None:
        raise LedgerError(
            "invalid_identifier",
            f"{field} must match {_ID_RE.pattern!r} and be at most {max_len} characters",
        )
    return raw


def parse_event(raw: Any, *, line_number: int | None = None) -> Event:
    where = f"line {line_number}: " if line_number is not None else ""
    if not isinstance(raw, Mapping):
        raise LedgerError("invalid_event", f"{where}event must be a JSON object")

    keys = set(raw)
    unknown = sorted(keys - _ALLOWED_FIELDS)
    missing = sorted(_REQUIRED_FIELDS - keys)
    if unknown:
        raise LedgerError("unknown_fields", f"{where}unknown fields: {', '.join(unknown)}")
    if missing:
        raise LedgerError("missing_fields", f"{where}missing fields: {', '.join(missing)}")

    version = raw["version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise LedgerError(
            "unsupported_version",
            f"{where}version must be integer {SCHEMA_VERSION}",
        )

    kind = raw["kind"]
    if not isinstance(kind, str) or kind not in EVENT_KINDS:
        raise LedgerError(
            "invalid_kind",
            f"{where}kind must be one of {', '.join(sorted(EVENT_KINDS))}",
        )

    artifact = raw.get("artifact")
    if artifact is not None:
        if not isinstance(artifact, str):
            raise LedgerError("invalid_artifact", f"{where}artifact must be a string")
        if not artifact or len(artifact) > 1000 or any(ord(ch) < 32 for ch in artifact):
            raise LedgerError(
                "invalid_artifact",
                f"{where}artifact must be 1..1000 printable characters",
            )

    try:
        return Event(
            version=version,
            event_id=_identifier(raw["event_id"], field=f"{where}event_id"),
            work_key=_identifier(raw["work_key"], field=f"{where}work_key"),
            kind=kind,
            owner=_identifier(raw["owner"], field=f"{where}owner", max_len=128),
            at=parse_time(raw["at"], field=f"{where}at"),
            artifact=artifact,
        )
    except LedgerError:
        raise


def _reject_nonfinite_json_constant(raw: str) -> None:
    raise LedgerError(
        "invalid_json_constant",
        f"non-finite JSON constant is not allowed: {raw}",
    )


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise LedgerError(
                "duplicate_json_key",
                f"duplicate JSON object key: {key}",
            )
        value[key] = item
    return value


def _strict_json_loads(line: str, *, line_number: int) -> Any:
    try:
        return json.loads(
            line,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except LedgerError as exc:
        raise LedgerError(exc.code, f"line {line_number}: {exc.message}") from exc
    except json.JSONDecodeError as exc:
        raise LedgerError(
            "invalid_json",
            f"line {line_number}: invalid JSON at column {exc.colno}",
        ) from exc


def parse_ndjson(lines: Iterable[str]) -> list[Event]:
    events: list[Event] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        raw = _strict_json_loads(line, line_number=line_number)
        events.append(parse_event(raw, line_number=line_number))
    return events


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _event_sort_key(event: Event) -> tuple[datetime, str]:
    return (event.at, event.event_id)


def build_report(
    events: Sequence[Event],
    *,
    as_of: datetime,
    lease_seconds: int,
) -> dict[str, Any]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise LedgerError("invalid_as_of", "as_of must be timezone-aware")
    as_of = as_of.astimezone(timezone.utc)
    if type(lease_seconds) is not int or lease_seconds <= 0:
        raise LedgerError("invalid_lease", "lease_seconds must be a positive integer")

    event_ids: set[str] = set()
    canonical_events = sorted(events, key=_event_sort_key)
    for event in canonical_events:
        if event.event_id in event_ids:
            raise LedgerError("duplicate_event_id", f"duplicate event_id: {event.event_id}")
        event_ids.add(event.event_id)
        if event.at > as_of:
            raise LedgerError(
                "future_event",
                f"event {event.event_id} occurs after the requested as_of timestamp",
            )

    work: dict[str, dict[str, Any]] = {}
    for event in canonical_events:
        state = work.setdefault(
            event.work_key,
            {
                "active": {},
                "history": [],
                "terminal": False,
                "last_release": None,
                "done_event": None,
            },
        )
        if state["terminal"]:
            raise LedgerError(
                "event_after_done",
                f"{event.work_key}: event {event.event_id} appears after DONE",
            )

        active: dict[str, dict[str, Any]] = state["active"]
        if event.kind == "TAKE":
            if event.owner in active:
                raise LedgerError(
                    "duplicate_take",
                    f"{event.work_key}: {event.owner} already has live custody",
                )
            active[event.owner] = {
                "owner": event.owner,
                "take_event_id": event.event_id,
                "taken_at": event.at,
                "last_event_id": event.event_id,
                "last_at": event.at,
                "artifact": event.artifact,
            }
        elif event.kind == "PROGRESS":
            if event.owner not in active:
                raise LedgerError(
                    "orphan_progress",
                    f"{event.work_key}: PROGRESS from {event.owner} has no live TAKE",
                )
            active[event.owner]["last_event_id"] = event.event_id
            active[event.owner]["last_at"] = event.at
            if event.artifact is not None:
                active[event.owner]["artifact"] = event.artifact
        elif event.kind == "RELEASE":
            if event.owner not in active:
                raise LedgerError(
                    "orphan_release",
                    f"{event.work_key}: RELEASE from {event.owner} has no live TAKE",
                )
            released = active.pop(event.owner)
            state["last_release"] = {
                "owner": event.owner,
                "event_id": event.event_id,
                "at": event.at,
                "artifact": event.artifact,
                "take_event_id": released["take_event_id"],
            }
        elif event.kind == "DONE":
            if set(active) != {event.owner}:
                owners = ", ".join(sorted(active)) or "none"
                raise LedgerError(
                    "ambiguous_done",
                    f"{event.work_key}: DONE by {event.owner} requires sole live custody; live owners: {owners}",
                )
            completed = active.pop(event.owner)
            state["done_event"] = {
                "owner": event.owner,
                "event_id": event.event_id,
                "at": event.at,
                "artifact": event.artifact,
                "take_event_id": completed["take_event_id"],
            }
            state["terminal"] = True

        state["history"].append(event.normalized())

    items: list[dict[str, Any]] = []
    counts = {name: 0 for name in DISPOSITIONS}

    for work_key in sorted(work):
        state = work[work_key]
        active_claims: list[dict[str, Any]] = []
        for owner in sorted(state["active"]):
            claim = state["active"][owner]
            age = (as_of - claim["last_at"]).total_seconds()
            if age < 0:
                raise LedgerError(
                    "future_event",
                    f"{work_key}: active event occurs after as_of",
                )
            active_claims.append(
                {
                    "owner": owner,
                    "take_event_id": claim["take_event_id"],
                    "taken_at": _format_time(claim["taken_at"]),
                    "last_event_id": claim["last_event_id"],
                    "last_at": _format_time(claim["last_at"]),
                    "age_seconds": age,
                    "stale": age > lease_seconds,
                    **(
                        {"artifact": claim["artifact"]}
                        if claim["artifact"] is not None
                        else {}
                    ),
                }
            )

        if state["terminal"]:
            disposition = "DONE"
        elif len(active_claims) > 1:
            disposition = "COLLISION"
        elif len(active_claims) == 1:
            disposition = "STALE" if active_claims[0]["stale"] else "ACTIVE"
        elif state["last_release"] is not None:
            disposition = "RELEASED"
        else:
            disposition = "OPEN"

        counts[disposition] += 1
        item: dict[str, Any] = {
            "work_key": work_key,
            "disposition": disposition,
            "active_claims": active_claims,
            "history": state["history"],
        }
        if state["last_release"] is not None:
            released = state["last_release"]
            item["last_release"] = {
                **released,
                "at": _format_time(released["at"]),
            }
        if state["done_event"] is not None:
            done = state["done_event"]
            item["done"] = {
                **done,
                "at": _format_time(done["at"]),
            }
        items.append(item)

    normalized_events = [event.normalized() for event in canonical_events]
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "as_of": _format_time(as_of),
        "lease_seconds": lease_seconds,
        "event_count": len(normalized_events),
        "work_count": len(items),
        "counts": counts,
        "events": normalized_events,
        "items": items,
    }
    receipt = hashlib.sha256(_canonical_json(body)).hexdigest()
    return {**body, "receipt_sha256": receipt}


def report_exit_code(report: Mapping[str, Any], *, allow_stale: bool) -> int:
    counts = report["counts"]
    if counts["COLLISION"]:
        return 3
    if counts["STALE"] and not allow_stale:
        return 4
    return 0


def _load_events(path: str, stdin: TextIO) -> list[Event]:
    if path == "-":
        return parse_ndjson(stdin)
    try:
        with Path(path).open("r", encoding="utf-8", newline="") as handle:
            return parse_ndjson(handle)
    except OSError as exc:
        raise LedgerError("input_error", f"cannot read {path}: {exc}") from exc


def _error_payload(exc: LedgerError) -> str:
    return json.dumps(
        {"error": {"code": exc.code, "message": exc.message}},
        sort_keys=True,
        separators=(",", ":"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Derive deterministic swarm custody state from NDJSON events."
    )
    parser.add_argument("input", help="NDJSON file path, or '-' for stdin")
    parser.add_argument(
        "--as-of",
        required=True,
        help="Timezone-aware ISO-8601 ledger evaluation timestamp",
    )
    parser.add_argument(
        "--lease-seconds",
        required=True,
        type=int,
        help="Positive lease duration; stale iff age is greater than this value",
    )
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="Return success for stale-but-non-colliding custody",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output (receipt remains canonical)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        events = _load_events(args.input, sys.stdin)
        as_of = parse_time(args.as_of, field="as_of")
        report = build_report(
            events,
            as_of=as_of,
            lease_seconds=args.lease_seconds,
        )
    except LedgerError as exc:
        print(_error_payload(exc), file=sys.stderr)
        return 2

    if args.pretty:
        print(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False))
    else:
        print(_canonical_json(report).decode("utf-8"))
    return report_exit_code(report, allow_stale=args.allow_stale)


if __name__ == "__main__":
    raise SystemExit(main())
