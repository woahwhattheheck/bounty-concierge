#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reconcile an offline swarm TAKE/CLAIM transcript into deterministic work ownership.

The tool performs no network requests and does not create provider claims. It is a
small collision fence for agents that share a coordination feed: the first active
claim for one canonical work key wins until that exact claim is released.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable

MAX_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_INPUTS = 8
MAX_MESSAGES = 10000

_ACQUIRE = {"TAKE", "CLAIM"}
_RELEASE = {"RELEASE", "DONE", "COMPLETE", "COMPLETED", "BLOCKED", "SHIPPED", "RECONCILED", "RETIRE", "RETIRED"}
_ACTION_RE = re.compile(
    r"\b(SOURCE[ _-]?COMPLETE|QA(?:[ _-]?(?:CLEAN|COMPLETE))?|PUBLISH(?:ED)?|"
    r"TAKE|CLAIM|RELEASE|DONE|COMPLETE|COMPLETED|BLOCKED|SHIPPED|RECONCILED|RETIRE|RETIRED)\b",
    re.I,
)
_EXPLICIT_WORK_RE = re.compile(r"\bwork[_ -]?key\s*[:=]\s*([^\s,;]+)", re.I)
_ISSUE_URL_RE = re.compile(r"https?://github\.com/([^/\s]+)/([^/\s]+)/issues/(\d+)", re.I)
_PR_URL_RE = re.compile(r"https?://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)", re.I)
_COMPACT_ISSUE_RE = re.compile(r"(?<![\w.-])([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#(\d+)\b")
_EXPLICIT_CLAIM_RE = re.compile(r"\bclaim[_ -]?id\s*[:=]\s*([^\s,;]+)", re.I)
_EXPLICIT_ACTOR_RE = re.compile(r"\b(?:owner|actor|identity)\s*[:=]\s*([^\s,;]+)", re.I)
_SPLIT_RE = re.compile(r"\s*[·|]\s*")


class FenceError(ValueError):
    """Invalid transcript or preflight arguments."""


@dataclass(frozen=True)
class Event:
    sequence: int
    action: str
    work_key: str
    claim_id: str
    actor: str | None
    ts: str | None
    permalink: str | None
    text: str

    def public(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "action": self.action,
            "work_key": self.work_key,
            "claim_id": self.claim_id,
            "actor": self.actor,
            "ts": self.ts,
            "permalink": self.permalink,
        }


def _read_bounded(path: Path) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise FenceError("transcript exceeds the 4 MiB limit")
    return raw


def _normalize_work_key(raw: str) -> str:
    value = raw.strip().strip("\x60<>()[]{}.,")
    lowered = value.lower()
    if lowered.startswith("swarm:"):
        match = re.fullmatch(
            r"swarm:(build|repair|qa|publish|metadata|claim|mutation):"
            r"github:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)([#!])([0-9]+)",
            value,
            re.I,
        )
        if not match or int(match.group(5)) < 1:
            raise FenceError(f"invalid swarm custody work key: {raw!r}")
        lane, owner, repo, separator, number = match.groups()
        return (
            f"swarm:{lane.lower()}:github:{owner.lower()}/{repo.lower()}"
            f"{separator}{int(number)}"
        )
    for prefix in ("operation:", "op:"):
        if lowered.startswith(prefix):
            operation = value[len(prefix):]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,239}", operation):
                raise FenceError(f"invalid operation work key: {raw!r}")
            return f"operation:{operation.lower()}"
    if lowered.startswith("github:"):
        value = value[7:]
    match = re.fullmatch(r"([^/\s]+)/([^#\s]+)#(\d+)", value)
    if not match:
        raise FenceError(f"invalid work key: {raw!r}")
    owner, repo, number = match.groups()
    return f"github:{owner.lower()}/{repo.lower()}#{int(number)}"


def extract_work_key(text: str) -> str:
    explicit = _EXPLICIT_WORK_RE.search(text)
    if explicit:
        return _normalize_work_key(explicit.group(1))

    url_matches = [*_ISSUE_URL_RE.findall(text), *_PR_URL_RE.findall(text)]
    url_keys = {
        _normalize_work_key(f"{owner}/{repo}#{number}")
        for owner, repo, number in url_matches
    }
    if len(url_keys) == 1:
        return next(iter(url_keys))
    if len(url_keys) > 1:
        raise FenceError("message contains multiple GitHub issue/PR URLs; add explicit work_key")

    compact_keys = {
        _normalize_work_key(f"{owner}/{repo}#{number}")
        for owner, repo, number in _COMPACT_ISSUE_RE.findall(text)
    }
    if len(compact_keys) == 1:
        return next(iter(compact_keys))
    if len(compact_keys) > 1:
        raise FenceError("message contains multiple compact issue keys; add explicit work_key")
    raise FenceError("message has no canonical GitHub issue work key")


def lifecycle_stage(text: str) -> str:
    """Classify one coordination headline without changing v1 ownership semantics."""
    first = text.splitlines()[0] if text.splitlines() else text
    upper = first.upper().replace("_", " ").replace("-", " ")
    # Terminal lifecycle labels win even when a handoff headline also names
    # TAKE/QA/PUBLISH. Otherwise a terminal release can accidentally acquire or
    # preserve ownership. Among non-terminal labels, TAKE/CLAIM must win over
    # progress words so "TAKE QA" and "TAKE ... PUBLISH" really claim the lane.
    if re.search(r"\bSHIPPED\b", upper):
        return "SHIPPED"
    if re.search(r"\bRETIRE(?:D)?\b", upper):
        return "RETIRE"
    if re.search(r"\b(?:RELEASE|DONE|COMPLETE|COMPLETED|BLOCKED|RECONCILED)\b", upper):
        return "RELEASE"
    if re.search(r"\b(?:TAKE|CLAIM)\b", upper):
        return "TAKE"
    if re.search(r"\bSOURCE\s+COMPLETE\b", upper):
        return "SOURCE_COMPLETE"
    if re.search(r"\bQA(?:\s+(?:CLEAN|COMPLETE))?\b", upper):
        return "QA"
    if re.search(r"\bPUBLISH(?:ED)?\b", upper):
        return "PUBLISH"
    raise FenceError("message first line has no recognized lifecycle action")


def extract_action(text: str) -> str:
    stage = lifecycle_stage(text)
    if stage == "TAKE":
        return "TAKE"
    if stage in {"RELEASE", "RETIRE", "SHIPPED"}:
        return "RELEASE"
    return "PROGRESS"


def _claim_id_from_first_line(text: str) -> str:
    explicit = _EXPLICIT_CLAIM_RE.search(text)
    if explicit:
        return explicit.group(1).strip("\x60.,")
    first = text.splitlines()[0].strip()
    parts = [part.strip() for part in _SPLIT_RE.split(first) if part.strip()]
    if len(parts) >= 2:
        candidate = parts[1]
    else:
        candidate = _ACTION_RE.sub("", first).strip(" /:-—")
    if not candidate:
        raise FenceError("message has no claim id; add claim_id=<id>")
    candidate = candidate.split(" — ", 1)[0].strip()
    if len(candidate) > 240:
        raise FenceError("claim id exceeds 240 characters")
    return candidate


def _parse_payload(raw: bytes) -> list[Any]:
    if len(raw) > MAX_BYTES:
        raise FenceError("transcript exceeds the 4 MiB limit")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeError as exc:
        raise FenceError("transcript is not valid UTF-8") from exc

    try:
        payload = json.loads(decoded)
    except ValueError:
        rows: list[Any] = []
        for line_number, line in enumerate(decoded.splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise FenceError(f"invalid JSON/NDJSON at line {line_number}") from exc
        payload = rows

    if isinstance(payload, dict):
        payload = payload.get("messages")
    if not isinstance(payload, list):
        raise FenceError("transcript must be a JSON list, NDJSON, or an object with messages")
    if len(payload) > MAX_MESSAGES:
        raise FenceError(f"transcript exceeds {MAX_MESSAGES} messages")
    return payload


def load_events(raw: bytes) -> tuple[list[Event], list[dict[str, Any]]]:
    rows = _parse_payload(raw)
    events: list[Event] = []
    ignored: list[dict[str, Any]] = []
    for sequence, row in enumerate(rows):
        if isinstance(row, str):
            text = row
            ts = permalink = actor = None
        elif isinstance(row, dict):
            text = row.get("text")
            ts = row.get("ts") or row.get("message_ts") or row.get("timestamp")
            permalink = row.get("permalink") or row.get("url")
            actor = row.get("actor") or row.get("owner") or row.get("identity")
            if ts is not None:
                ts = str(ts)
            if permalink is not None:
                permalink = str(permalink)
            if actor is not None:
                actor = str(actor)
        else:
            ignored.append({"sequence": sequence, "reason": "row is not a string/object"})
            continue
        if not isinstance(text, str) or not text.strip():
            ignored.append({"sequence": sequence, "reason": "missing text"})
            continue
        try:
            action = extract_action(text)
            work_key = extract_work_key(text)
            claim_id = _claim_id_from_first_line(text)
        except FenceError as exc:
            ignored.append({"sequence": sequence, "reason": str(exc)})
            continue
        if actor is None:
            match = _EXPLICIT_ACTOR_RE.search(text)
            actor = match.group(1) if match else None
        events.append(Event(sequence, action, work_key, claim_id, actor, ts, permalink, text))
    return events, ignored


def _numeric_slack_ts(ts: str | None) -> Decimal | None:
    if ts is None:
        return None
    try:
        value = Decimal(ts)
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() and value >= 0 else None


def merge_event_streams(streams: Iterable[Iterable[Event]]) -> tuple[list[Event], int]:
    """Merge overlapping transcript snapshots without trusting one view's ordering.

    Slack search can lag a recent channel-tail read (or vice versa). Callers can
    therefore supply both snapshots. Stable message identities are deduplicated,
    and when every event has a numeric Slack timestamp the combined stream is
    ordered by that timestamp before ownership is reconciled.
    """
    flattened: list[tuple[int, Event]] = []
    seen: set[tuple[str, ...]] = set()
    duplicates = 0
    ingest = 0

    for stream in streams:
        for event in stream:
            identities: list[tuple[str, ...]] = []
            if event.permalink:
                identities.append(("permalink", event.permalink))
            if event.ts:
                identities.append(("ts_text", event.ts, event.text))
            if not identities:
                identities.append(("ingest", str(ingest)))

            if any(identity in seen for identity in identities):
                duplicates += 1
                ingest += 1
                continue
            seen.update(identities)
            flattened.append((ingest, event))
            ingest += 1

    if len(flattened) > MAX_MESSAGES:
        raise FenceError(f"combined transcript exceeds {MAX_MESSAGES} messages")

    stamped = [(_numeric_slack_ts(event.ts), ingest, event) for ingest, event in flattened]
    if stamped and all(ts is not None for ts, _, _ in stamped):
        stamped.sort(key=lambda item: (item[0], item[1]))
        ordered = [event for _, _, event in stamped]
    else:
        ordered = [event for _, event in flattened]

    return [replace(event, sequence=sequence) for sequence, event in enumerate(ordered)], duplicates


def reconcile(events: Iterable[Event]) -> dict[str, Any]:
    """Apply first-active-claim-wins with exact-claim releases in transcript order."""
    active: dict[str, Event] = {}
    conflicts: list[dict[str, Any]] = []
    reassertions: list[dict[str, Any]] = []
    releases: list[dict[str, Any]] = []
    unmatched_releases: list[dict[str, Any]] = []
    progress: list[dict[str, Any]] = []
    orphan_progress: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []

    for event in events:
        current = active.get(event.work_key)
        if event.action == "PROGRESS":
            if current is not None and current.claim_id == event.claim_id:
                progress.append({"event": event.public(), "active": current.public()})
            else:
                orphan_progress.append({
                    "event": event.public(),
                    "active": current.public() if current else None,
                })
            continue
        if event.action == "TAKE":
            if current is None:
                active[event.work_key] = event
                accepted.append(event.public())
            elif current.claim_id == event.claim_id:
                reassertions.append({"event": event.public(), "active": current.public()})
            else:
                conflicts.append({"event": event.public(), "winner": current.public()})
            continue

        if current is not None and current.claim_id == event.claim_id:
            releases.append({"event": event.public(), "released": current.public()})
            del active[event.work_key]
        else:
            unmatched_releases.append({
                "event": event.public(),
                "active": current.public() if current else None,
            })

    return {
        "schema": "swarm-claim-fence/v1",
        "active": {key: active[key].public() for key in sorted(active)},
        "accepted_claims": accepted,
        "conflicts": conflicts,
        "reassertions": reassertions,
        "releases": releases,
        "unmatched_releases": unmatched_releases,
        "progress": progress,
        "orphan_progress": orphan_progress,
        "counts": {
            "accepted_claims": len(accepted),
            "active_claims": len(active),
            "conflicts": len(conflicts),
            "reassertions": len(reassertions),
            "releases": len(releases),
            "unmatched_releases": len(unmatched_releases),
            "progress": len(progress),
            "orphan_progress": len(orphan_progress),
        },
        "interpretation": [
            "The first active TAKE/CLAIM for one canonical work key wins in transcript order.",
            "Only the exact winning claim_id releases that ownership; ambiguous releases fail closed.",
            "A conflict does not become active ownership and should be released/reconciled by that worker.",
            "This is an offline coordination fence, not a provider assignment, payment claim, or distributed lock.",
            "Refresh the shared coordination feed immediately before claiming and again after posting the claim.",
        ],
    }


def preflight(report: dict[str, Any], work_key: str, claim_id: str) -> dict[str, Any]:
    key = _normalize_work_key(work_key)
    claim = claim_id.strip()
    if not claim:
        raise FenceError("claim-id must not be empty")
    current = report["active"].get(key)
    if current is None:
        status = "TAKE_ALLOWED"
    elif current["claim_id"] == claim:
        status = "ALREADY_OWNED"
    else:
        status = "COLLISION"
    return {
        "schema": "swarm-claim-preflight/v1",
        "status": status,
        "work_key": key,
        "claim_id": claim,
        "active_claim": current,
    }


def durable_report(
    events: Iterable[Event],
    *,
    history_complete: bool,
    ignored: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Summarize durable lifecycle evidence without performing network I/O.

    BUILD_ALLOWED is intentionally conservative: callers must explicitly attest
    that the supplied transcript window is complete, there must be no ambiguous
    ignored coordination rows, and the proposed claim must already be the active
    owner before source work begins.
    """
    rows = list(events)
    by_work: dict[str, list[dict[str, Any]]] = {}
    for event in rows:
        record = event.public()
        record["stage"] = lifecycle_stage(event.text)
        by_work.setdefault(event.work_key, []).append(record)

    ambiguity = [
        item for item in ignored
        if "multiple" in str(item.get("reason", "")).lower()
        or "ambiguous" in str(item.get("reason", "")).lower()
    ]
    reconciled = reconcile(rows)
    return {
        "schema": "swarm-durable-ownership/v1",
        "history_complete": history_complete,
        "ambiguity_evidence": ambiguity,
        "work": {key: by_work[key] for key in sorted(by_work)},
        "active": reconciled["active"],
        "conflicts": reconciled["conflicts"],
        "counts": {
            "work_keys": len(by_work),
            "ambiguities": len(ambiguity),
            "conflicts": reconciled["counts"]["conflicts"],
            "active_claims": reconciled["counts"]["active_claims"],
            "progress": reconciled["counts"]["progress"],
            "orphan_progress": reconciled["counts"]["orphan_progress"],
        },
    }


def durable_preflight(
    durable: dict[str, Any],
    *,
    work_key: str,
    claim_id: str,
) -> dict[str, Any]:
    key = _normalize_work_key(work_key)
    claim = claim_id.strip()
    if not claim:
        raise FenceError("claim-id must not be empty")

    active = durable["active"].get(key)
    if not durable["history_complete"]:
        status = "HISTORY_INCOMPLETE"
        build_allowed = False
        reason = "caller has not attested that the supplied coordination history is complete"
    elif durable["ambiguity_evidence"]:
        status = "AMBIGUOUS_HISTORY"
        build_allowed = False
        reason = "coordination history contains ambiguous work identity; reconcile it first"
    elif active is None:
        status = "CLAIM_REQUIRED"
        build_allowed = False
        reason = "no active claim exists; post and re-read the TAKE before building"
    elif active["claim_id"] != claim:
        status = "COLLISION"
        build_allowed = False
        reason = "another active claim owns the canonical work key"
    else:
        status = "BUILD_ALLOWED"
        build_allowed = True
        reason = "complete unambiguous history confirms this exact claim as active owner"

    return {
        "schema": "swarm-durable-preflight/v1",
        "status": status,
        "build_allowed": build_allowed,
        "work_key": key,
        "claim_id": claim,
        "active_claim": active,
        "reason": reason,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        action="append",
        help=(
            "JSON/NDJSON coordination transcript; repeat to combine views "
            "(for Slack, prefer search export + recent channel-tail export)"
        ),
    )
    parser.add_argument("--work-key", help="optional OWNER/REPO#N or op:<operation-id> key for a proposed claim")
    parser.add_argument("--claim-id", help="proposed claim id; requires --work-key")
    parser.add_argument("--output", type=Path, help="write JSON instead of stdout")
    parser.add_argument(
        "--durable",
        action="store_true",
        help="also emit durable lifecycle evidence and a fail-closed build preflight",
    )
    parser.add_argument(
        "--history-complete",
        action="store_true",
        help="attest that the supplied transcript window is complete (used only with --durable)",
    )
    args = parser.parse_args(argv)
    try:
        if bool(args.work_key) != bool(args.claim_id):
            raise FenceError("--work-key and --claim-id must be supplied together")
        if len(args.input) > MAX_INPUTS:
            raise FenceError(f"no more than {MAX_INPUTS} --input files are allowed")

        raws = [_read_bounded(path) for path in args.input]
        if sum(len(raw) for raw in raws) > MAX_TOTAL_BYTES:
            raise FenceError("combined transcripts exceed the 8 MiB limit")

        streams: list[list[Event]] = []
        ignored: list[dict[str, Any]] = []
        for path, raw in zip(args.input, raws):
            stream, stream_ignored = load_events(raw)
            streams.append(stream)
            ignored.extend({**item, "source": str(path)} for item in stream_ignored)

        events, duplicates = merge_event_streams(streams)
        report = reconcile(events)
        report["sources_loaded"] = len(args.input)
        report["messages_parsed"] = len(events)
        report["duplicate_messages_dropped"] = duplicates
        report["messages_ignored"] = ignored
        if args.work_key:
            report["preflight"] = preflight(report, args.work_key, args.claim_id)
        if args.durable:
            durable = durable_report(
                events,
                history_complete=args.history_complete,
                ignored=ignored,
            )
            report["durable"] = durable
            if args.work_key:
                report["durable_preflight"] = durable_preflight(
                    durable,
                    work_key=args.work_key,
                    claim_id=args.claim_id,
                )
        rendered = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    except (FenceError, OSError) as exc:
        print(f"swarm-claim-fence: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
