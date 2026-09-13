"""Offline, evidence-bound remediation packets for explicit paid-work feedback."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

INPUT_SCHEMA = "bounty-feedback-remediation-input/v1"
ADVANCE_SCHEMA = "bounty-feedback-remediation-advance/v1"
PACKET_SCHEMA = "bounty-feedback-remediation-packet/v1"
MAX_JSON_BYTES = 1_000_000
MAX_EVENTS = 500
MAX_OBLIGATIONS = 2_000
MAX_SUMMARY_CHARS = 4_000
MAX_NOTE_CHARS = 2_000
MAX_ID_CHARS = 200
MAX_SOURCE_CHARS = 500
MAX_HINTS = 20
MAX_PATH_CHARS = 512
MAX_DEPTH = 24
MAX_CONTAINER_ITEMS = 5_000
MAX_INT_ABS = 10**12
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#@+-]{0,199}$")
AUTHORITIES = {"GITHUB_OWNER", "GITHUB_MEMBER", "GITHUB_COLLABORATOR", "VERIFIED_SPONSOR"}
KINDS = {"CHANGES_REQUESTED", "ACTIONABLE_COMMENT", "NEEDS_CHANGES"}
DISPOSITIONS = {"REQUIRED", "CLARIFY", "NON_BLOCKING"}
AUTHORITY = {
    "external_send_performed": False,
    "feedback_authority_inferred": False,
    "maintainer_intent_inferred": False,
    "scope_change_authorized": False,
    "acceptance_inferred": False,
    "payment_inferred": False,
    "payout_requested": False,
    "wallet_mutated": False,
    "revenue_recognized": False,
}


class FeedbackRemediationError(ValueError):
    pass


def fail(message: str) -> None:
    raise FeedbackRemediationError(message)


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FeedbackRemediationError("value is not canonical JSON") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def reject_constant(value: str) -> None:
    fail("non-finite JSON number is forbidden: %s" % value)


def reject_float(value: str) -> None:
    fail("floating-point JSON numbers are forbidden: %s" % value)


def parse_int(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > 20:
        fail("JSON integer out of bounds")
    try:
        result = int(value)
    except ValueError as exc:
        raise FeedbackRemediationError("invalid JSON integer") from exc
    if abs(result) > MAX_INT_ABS:
        fail("JSON integer out of bounds")
    return result


def unique_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            fail("duplicate JSON key: %s" % key)
        out[key] = value
    return out


def validate_shape(value: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        fail("JSON nesting too deep")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if len(value) > MAX_JSON_BYTES:
            fail("JSON string too large")
        return
    if isinstance(value, int):
        if abs(value) > MAX_INT_ABS:
            fail("JSON integer out of bounds")
        return
    if isinstance(value, float):
        fail("floating-point JSON numbers are not accepted")
    if isinstance(value, list):
        if len(value) > MAX_CONTAINER_ITEMS:
            fail("JSON list too large")
        for item in value:
            validate_shape(item, depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_CONTAINER_ITEMS:
            fail("JSON object too large")
        for key, item in value.items():
            if not isinstance(key, str):
                fail("JSON object key must be text")
            validate_shape(item, depth + 1)
        return
    fail("unsupported JSON value")


def strict_json_loads(text: str) -> Any:
    if not isinstance(text, str):
        fail("JSON input must be text")
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        fail("JSON input exceeds byte limit")
    try:
        value = json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant,
                           parse_float=reject_float, parse_int=parse_int)
    except FeedbackRemediationError:
        raise
    except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise FeedbackRemediationError("invalid JSON") from exc
    validate_shape(value)
    return value


def load_json(path: str) -> Any:
    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        fail("JSON input exceeds byte limit")
    try:
        return strict_json_loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise FeedbackRemediationError("JSON input must be UTF-8") from exc


def obj(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        fail("%s must be an object" % name)
    return value


def arr(value: Any, name: str, limit: int) -> Sequence[Any]:
    if not isinstance(value, list):
        fail("%s must be a list" % name)
    if len(value) > limit:
        fail("%s exceeds item limit" % name)
    return value


def exact(value: Mapping[str, Any], required: Iterable[str], name: str) -> None:
    want, got = set(required), set(value)
    if got != want:
        fail("%s keys mismatch; missing=%s extra=%s" %
             (name, sorted(want - got), sorted(got - want)))


def text(value: Any, name: str, limit: int, *, one_line: bool = False) -> str:
    if not isinstance(value, str):
        fail("%s must be text" % name)
    result = value.strip()
    if not result:
        fail("%s cannot be empty" % name)
    if len(result) > limit:
        fail("%s exceeds character limit" % name)
    if one_line and any(ch in result for ch in ("\n", "\r", "\x00")):
        fail("%s must be a single safe line" % name)
    return result


def safe_id(value: Any, name: str) -> str:
    result = text(value, name, MAX_ID_CHARS, one_line=True)
    if not SAFE_ID.fullmatch(result):
        fail("%s contains unsupported characters" % name)
    return result


def h40(value: Any, name: str) -> str:
    if not isinstance(value, str) or not HEX40.fullmatch(value):
        fail("%s must be 40 lowercase hex characters" % name)
    return value


def h64(value: Any, name: str) -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        fail("%s must be 64 lowercase hex characters" % name)
    return value


def positive(value: Any, name: str, maximum: int = MAX_INT_ABS) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > maximum:
        fail("%s must be a positive bounded integer" % name)
    return value


def instant(value: Any, name: str) -> str:
    raw = text(value, name, 80, one_line=True)
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise FeedbackRemediationError("%s must be ISO-8601" % name) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        fail("%s must include a timezone" % name)
    parsed = parsed.astimezone(timezone.utc)
    rendered = parsed.isoformat(timespec="microseconds" if parsed.microsecond else "seconds")
    return rendered.replace("+00:00", "Z")


def norm_submission(value: Any) -> Dict[str, Any]:
    item = obj(value, "submission")
    keys = {"submission_id", "canonical_source", "submitted_head_sha", "artifact_revision",
            "artifact_evidence_digest", "submission_digest"}
    exact(item, keys, "submission")
    optional = item["submission_digest"]
    if optional is not None:
        optional = h64(optional, "submission.submission_digest")
    return {
        "submission_id": safe_id(item["submission_id"], "submission.submission_id"),
        "canonical_source": text(item["canonical_source"], "submission.canonical_source",
                                 MAX_SOURCE_CHARS, one_line=True),
        "submitted_head_sha": h40(item["submitted_head_sha"], "submission.submitted_head_sha"),
        "artifact_revision": positive(item["artifact_revision"], "submission.artifact_revision"),
        "artifact_evidence_digest": h64(item["artifact_evidence_digest"],
                                        "submission.artifact_evidence_digest"),
        "submission_digest": optional,
    }


def norm_hint(value: Any, index: int) -> Dict[str, Any]:
    name = "feedback.hints[%d]" % index
    item = obj(value, name)
    exact(item, {"path", "line_start", "line_end"}, name)
    path = text(item["path"], name + ".path", MAX_PATH_CHARS, one_line=True)
    pure = PurePosixPath(path)
    if pure.is_absolute() or not pure.parts or any(part == ".." for part in pure.parts):
        fail("feedback hint path must be a normalized relative POSIX path")
    if "\\" in path or pure.as_posix() != path:
        fail("feedback hint path must use normalized POSIX separators")
    start = positive(item["line_start"], name + ".line_start", 10**9)
    end = positive(item["line_end"], name + ".line_end", 10**9)
    if end < start:
        fail("feedback hint line_end precedes line_start")
    return {"path": path, "line_start": start, "line_end": end}


def norm_event(value: Any, submission: Mapping[str, Any], name: str = "feedback") -> Dict[str, Any]:
    item = obj(value, name)
    keys = {"source_event_id", "authority", "kind", "occurred_at", "evidence_digest", "summary",
            "disposition", "target_head_sha", "target_artifact_revision", "hints"}
    exact(item, keys, name)
    authority = text(item["authority"], name + ".authority", 64, one_line=True)
    kind = text(item["kind"], name + ".kind", 64, one_line=True)
    disposition = text(item["disposition"], name + ".disposition", 64, one_line=True)
    if authority not in AUTHORITIES:
        fail("%s.authority is unsupported" % name)
    if kind not in KINDS:
        fail("%s.kind is unsupported" % name)
    if disposition not in DISPOSITIONS:
        fail("%s.disposition is unsupported" % name)
    head = h40(item["target_head_sha"], name + ".target_head_sha")
    revision = positive(item["target_artifact_revision"], name + ".target_artifact_revision")
    if head != submission["submitted_head_sha"]:
        fail("%s is bound to a different submitted head" % name)
    if revision != submission["artifact_revision"]:
        fail("%s is bound to a different artifact revision" % name)
    hints = [norm_hint(x, i) for i, x in enumerate(arr(item["hints"], name + ".hints", MAX_HINTS))]
    hints.sort(key=lambda x: (x["path"], x["line_start"], x["line_end"]))
    return {
        "source_event_id": safe_id(item["source_event_id"], name + ".source_event_id"),
        "authority": authority,
        "kind": kind,
        "occurred_at": instant(item["occurred_at"], name + ".occurred_at"),
        "evidence_digest": h64(item["evidence_digest"], name + ".evidence_digest"),
        "summary": text(item["summary"], name + ".summary", MAX_SUMMARY_CHARS),
        "disposition": disposition,
        "target_head_sha": head,
        "target_artifact_revision": revision,
        "hints": hints,
    }


def event_fp(event: Mapping[str, Any]) -> str:
    return digest(event)


def obligation_id(source: str, event: Mapping[str, Any]) -> str:
    material = {"canonical_source": source, "source_event_id": event["source_event_id"],
                "target_head_sha": event["target_head_sha"],
                "target_artifact_revision": event["target_artifact_revision"],
                "evidence_digest": event["evidence_digest"]}
    return "obl-" + digest(material)[:24]


def obligation(source: str, event: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "obligation_id": obligation_id(source, event), "source_event_id": event["source_event_id"],
        "event_fingerprint": event_fp(event), "authority": event["authority"], "kind": event["kind"],
        "occurred_at": event["occurred_at"], "evidence_digest": event["evidence_digest"],
        "summary": event["summary"], "disposition": event["disposition"],
        "blocking": event["disposition"] != "NON_BLOCKING", "target_head_sha": event["target_head_sha"],
        "target_artifact_revision": event["target_artifact_revision"], "hints": copy.deepcopy(event["hints"]),
        "state": "OPEN", "resolution": None,
    }


def norm_events(values: Any, submission: Mapping[str, Any], existing: Optional[Mapping[str, str]] = None) -> List[Dict[str, Any]]:
    prior = dict(existing or {})
    by_id: Dict[str, Dict[str, Any]] = {}
    for i, raw in enumerate(arr(values, "feedback", MAX_EVENTS)):
        event = norm_event(raw, submission, "feedback[%d]" % i)
        eid, fp = event["source_event_id"], event_fp(event)
        if eid in prior:
            if prior[eid] != fp:
                fail("source_event_id conflicts with previously bound event: %s" % eid)
            continue
        if eid in by_id:
            if event_fp(by_id[eid]) != fp:
                fail("duplicate source_event_id has conflicting bytes: %s" % eid)
            continue
        by_id[eid] = event
    return sorted(by_id.values(), key=lambda e: (e["target_artifact_revision"], e["occurred_at"],
                                                  e["source_event_id"], e["evidence_digest"]))


def counts(items: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    return {
        "total": len(items),
        "open": sum(x["state"] == "OPEN" for x in items),
        "addressed": sum(x["state"] == "ADDRESSED" for x in items),
        "open_required": sum(x["state"] == "OPEN" and x["disposition"] == "REQUIRED" for x in items),
        "open_clarify": sum(x["state"] == "OPEN" and x["disposition"] == "CLARIFY" for x in items),
        "open_non_blocking": sum(x["state"] == "OPEN" and x["disposition"] == "NON_BLOCKING" for x in items),
    }


def build_packet(submission: Mapping[str, Any], items: Sequence[Mapping[str, Any]], generation: int,
                 predecessor: Optional[str]) -> Dict[str, Any]:
    ordered = sorted((copy.deepcopy(x) for x in items),
                     key=lambda x: (x["target_artifact_revision"], x["occurred_at"],
                                    x["source_event_id"], x["obligation_id"]))
    if len(ordered) > MAX_OBLIGATIONS:
        fail("obligation history exceeds item limit")
    packet = {"schema": PACKET_SCHEMA, "generation": generation, "submission": copy.deepcopy(dict(submission)),
              "predecessor_packet_digest": predecessor, "obligations": ordered, "counts": counts(ordered),
              "authority": copy.deepcopy(AUTHORITY), "packet_digest": ""}
    packet["packet_digest"] = digest(packet)
    return packet


def compile_remediation(value: Any) -> Dict[str, Any]:
    root = obj(value, "input")
    exact(root, {"schema", "submission", "feedback"}, "input")
    if root["schema"] != INPUT_SCHEMA:
        fail("unsupported input schema")
    submission = norm_submission(root["submission"])
    events = norm_events(root["feedback"], submission)
    if not events:
        fail("at least one explicit feedback event is required")
    return build_packet(submission, [obligation(submission["canonical_source"], e) for e in events], 1, None)


def norm_resolution(value: Any, index: int, successor: Mapping[str, Any]) -> Dict[str, Any]:
    name = "addressed[%d]" % index
    item = obj(value, name)
    exact(item, {"obligation_id", "evidence_digest", "note"}, name)
    note = item["note"]
    if note is not None:
        note = text(note, name + ".note", MAX_NOTE_CHARS)
    return {"obligation_id": safe_id(item["obligation_id"], name + ".obligation_id"),
            "evidence_digest": h64(item["evidence_digest"], name + ".evidence_digest"),
            "successor_head_sha": successor["submitted_head_sha"],
            "successor_artifact_revision": successor["artifact_revision"], "note": note}


def advance_remediation(value: Any) -> Dict[str, Any]:
    root = obj(value, "advance input")
    exact(root, {"schema", "prior_packet", "successor_submission", "addressed", "new_feedback"}, "advance input")
    if root["schema"] != ADVANCE_SCHEMA:
        fail("unsupported advance schema")
    prior = verify_packet(root["prior_packet"])
    successor = norm_submission(root["successor_submission"])
    previous = prior["submission"]
    if successor["canonical_source"] != previous["canonical_source"]:
        fail("successor canonical_source differs from prior packet")
    if successor["artifact_revision"] <= previous["artifact_revision"]:
        fail("successor artifact_revision must increase")
    if successor["submitted_head_sha"] == previous["submitted_head_sha"]:
        fail("successor submitted_head_sha must change")
    if successor["artifact_evidence_digest"] == previous["artifact_evidence_digest"]:
        fail("successor artifact evidence must change")
    items = copy.deepcopy(prior["obligations"])
    by_id = {x["obligation_id"]: x for x in items}
    if len(by_id) != len(items):
        fail("prior packet contains duplicate obligation IDs")
    seen = set()
    for i, raw in enumerate(arr(root["addressed"], "addressed", MAX_OBLIGATIONS)):
        resolution = norm_resolution(raw, i, successor)
        oid = resolution["obligation_id"]
        if oid in seen:
            fail("duplicate addressed obligation_id: %s" % oid)
        seen.add(oid)
        if oid not in by_id:
            fail("cannot address unknown obligation: %s" % oid)
        target = by_id[oid]
        if target["state"] != "OPEN":
            fail("obligation is already addressed: %s" % oid)
        target["state"] = "ADDRESSED"
        target["resolution"] = {k: resolution[k] for k in
                                ("evidence_digest", "successor_head_sha", "successor_artifact_revision", "note")}
    existing = {x["source_event_id"]: x["event_fingerprint"] for x in items}
    for event in norm_events(root["new_feedback"], successor, existing):
        fresh = obligation(successor["canonical_source"], event)
        if fresh["obligation_id"] in by_id:
            fail("new feedback collides with an existing obligation")
        items.append(fresh)
        by_id[fresh["obligation_id"]] = fresh
    return build_packet(successor, items, prior["generation"] + 1, prior["packet_digest"])


def norm_packet_obligation(value: Any, index: int, source: str) -> Dict[str, Any]:
    name = "packet.obligations[%d]" % index
    item = obj(value, name)
    keys = {"obligation_id", "source_event_id", "event_fingerprint", "authority", "kind", "occurred_at",
            "evidence_digest", "summary", "disposition", "blocking", "target_head_sha",
            "target_artifact_revision", "hints", "state", "resolution"}
    exact(item, keys, name)
    synthetic = {"submitted_head_sha": h40(item["target_head_sha"], name + ".target_head_sha"),
                 "artifact_revision": positive(item["target_artifact_revision"], name + ".target_artifact_revision")}
    event = norm_event({k: item[k] for k in ("source_event_id", "authority", "kind", "occurred_at",
                                              "evidence_digest", "summary", "disposition", "target_head_sha",
                                              "target_artifact_revision", "hints")}, synthetic, name + ".event")
    fp, oid = event_fp(event), obligation_id(source, event)
    if item["event_fingerprint"] != fp or item["obligation_id"] != oid:
        fail("%s evidence identity mismatch" % name)
    expected_blocking = event["disposition"] != "NON_BLOCKING"
    if not isinstance(item["blocking"], bool) or item["blocking"] != expected_blocking:
        fail("%s blocking flag mismatch" % name)
    state = item["state"]
    if state not in ("OPEN", "ADDRESSED"):
        fail("%s state is unsupported" % name)
    resolution = item["resolution"]
    normalized_resolution = None
    if state == "OPEN":
        if resolution is not None:
            fail("%s open obligation cannot have a resolution" % name)
    else:
        data = obj(resolution, name + ".resolution")
        exact(data, {"evidence_digest", "successor_head_sha", "successor_artifact_revision", "note"},
              name + ".resolution")
        note = data["note"]
        if note is not None:
            note = text(note, name + ".resolution.note", MAX_NOTE_CHARS)
        normalized_resolution = {
            "evidence_digest": h64(data["evidence_digest"], name + ".resolution.evidence_digest"),
            "successor_head_sha": h40(data["successor_head_sha"], name + ".resolution.successor_head_sha"),
            "successor_artifact_revision": positive(data["successor_artifact_revision"],
                                                    name + ".resolution.successor_artifact_revision"),
            "note": note,
        }
        if normalized_resolution["successor_artifact_revision"] <= event["target_artifact_revision"]:
            fail("%s resolution must bind a later artifact revision" % name)
        if normalized_resolution["successor_head_sha"] == event["target_head_sha"]:
            fail("%s resolution must bind a different successor head" % name)
    return {"obligation_id": oid, "source_event_id": event["source_event_id"], "event_fingerprint": fp,
            "authority": event["authority"], "kind": event["kind"], "occurred_at": event["occurred_at"],
            "evidence_digest": event["evidence_digest"], "summary": event["summary"],
            "disposition": event["disposition"], "blocking": expected_blocking,
            "target_head_sha": event["target_head_sha"], "target_artifact_revision": event["target_artifact_revision"],
            "hints": event["hints"], "state": state, "resolution": normalized_resolution}


def verify_packet(value: Any) -> Dict[str, Any]:
    packet = obj(value, "packet")
    keys = {"schema", "generation", "submission", "predecessor_packet_digest", "obligations", "counts",
            "authority", "packet_digest"}
    exact(packet, keys, "packet")
    if packet["schema"] != PACKET_SCHEMA:
        fail("unsupported packet schema")
    generation = positive(packet["generation"], "packet.generation")
    submission = norm_submission(packet["submission"])
    predecessor = packet["predecessor_packet_digest"]
    if generation == 1:
        if predecessor is not None:
            fail("generation 1 cannot have a predecessor")
    else:
        predecessor = h64(predecessor, "packet.predecessor_packet_digest")
    items = [norm_packet_obligation(x, i, submission["canonical_source"])
             for i, x in enumerate(arr(packet["obligations"], "packet.obligations", MAX_OBLIGATIONS))]
    ordered = sorted(items, key=lambda x: (x["target_artifact_revision"], x["occurred_at"],
                                           x["source_event_id"], x["obligation_id"]))
    if items != ordered:
        fail("packet obligations are not in canonical order")
    if len({x["obligation_id"] for x in items}) != len(items):
        fail("packet contains duplicate obligation IDs")
    if len({x["source_event_id"] for x in items}) != len(items):
        fail("packet contains duplicate source_event_id values")
    if packet["counts"] != counts(items):
        fail("packet counts do not match obligations")
    if packet["authority"] != AUTHORITY:
        fail("packet authority ceiling mismatch")
    for item in items:
        if item["target_artifact_revision"] > submission["artifact_revision"]:
            fail("obligation targets a future artifact revision")
        resolution = item["resolution"]
        if resolution is not None:
            if resolution["successor_artifact_revision"] > submission["artifact_revision"]:
                fail("resolution targets a future artifact revision")
            if (resolution["successor_artifact_revision"] == submission["artifact_revision"] and
                    resolution["successor_head_sha"] != submission["submitted_head_sha"]):
                fail("current-revision resolution head differs from current submission")
    packet_digest = h64(packet["packet_digest"], "packet.packet_digest")
    normalized = {"schema": PACKET_SCHEMA, "generation": generation, "submission": submission,
                  "predecessor_packet_digest": predecessor, "obligations": items, "counts": counts(items),
                  "authority": copy.deepcopy(AUTHORITY), "packet_digest": ""}
    if packet_digest != digest(normalized):
        fail("packet digest mismatch")
    normalized["packet_digest"] = packet_digest
    return normalized


def immutable(item: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in item.items() if k not in {"state", "resolution"}}


def verify_chain(values: Sequence[Any]) -> List[Dict[str, Any]]:
    raw = arr(list(values), "packet chain", MAX_OBLIGATIONS)
    if not raw:
        fail("packet chain cannot be empty")
    verified = [verify_packet(x) for x in raw]
    for index, packet in enumerate(verified):
        if packet["generation"] != index + 1:
            fail("packet chain generations must be contiguous from 1")
        if index == 0:
            if packet["predecessor_packet_digest"] is not None:
                fail("packet chain root has predecessor")
            continue
        previous = verified[index - 1]
        if packet["predecessor_packet_digest"] != previous["packet_digest"]:
            fail("packet chain predecessor digest mismatch")
        current_submission, old_submission = packet["submission"], previous["submission"]
        if current_submission["canonical_source"] != old_submission["canonical_source"]:
            fail("packet chain canonical source changed")
        if current_submission["artifact_revision"] <= old_submission["artifact_revision"]:
            fail("packet chain artifact revision did not increase")
        if current_submission["submitted_head_sha"] == old_submission["submitted_head_sha"]:
            fail("packet chain submitted head did not change")
        if current_submission["artifact_evidence_digest"] == old_submission["artifact_evidence_digest"]:
            fail("packet chain artifact evidence did not change")
        old_by_id = {x["obligation_id"]: x for x in previous["obligations"]}
        new_by_id = {x["obligation_id"]: x for x in packet["obligations"]}
        if not set(old_by_id).issubset(new_by_id):
            fail("packet chain dropped a prior obligation")
        for oid, old in old_by_id.items():
            current = new_by_id[oid]
            if immutable(current) != immutable(old):
                fail("packet chain rewrote prior obligation evidence: %s" % oid)
            if old["state"] == "ADDRESSED":
                if current != old:
                    fail("packet chain rewrote an addressed obligation: %s" % oid)
                continue
            if current["state"] == "OPEN":
                if current != old:
                    fail("packet chain mutated an unresolved obligation: %s" % oid)
                continue
            resolution = current["resolution"]
            if resolution is None:
                fail("packet chain addressed obligation without resolution evidence")
            if resolution["successor_artifact_revision"] != current_submission["artifact_revision"]:
                fail("packet chain resolution is not bound to current artifact revision")
            if resolution["successor_head_sha"] != current_submission["submitted_head_sha"]:
                fail("packet chain resolution is not bound to current submitted head")
        for oid, item in new_by_id.items():
            if oid in old_by_id:
                continue
            if item["target_artifact_revision"] != current_submission["artifact_revision"]:
                fail("packet chain injected a backdated obligation")
            if item["target_head_sha"] != current_submission["submitted_head_sha"]:
                fail("packet chain injected obligation for a different head")
            if item["state"] != "OPEN" or item["resolution"] is not None:
                fail("new packet-chain obligation must begin OPEN")
    return verified


def write_json(value: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compile or verify evidence-bound bounty feedback remediation packets.")
    subs = parser.add_subparsers(dest="command", required=True)
    for command, argument in (("compile", "input"), ("advance", "input"), ("verify", "packet")):
        sub = subs.add_parser(command)
        sub.add_argument(argument)
    args = parser.parse_args(argv)
    try:
        if args.command == "compile":
            write_json(compile_remediation(load_json(args.input)))
        elif args.command == "advance":
            write_json(advance_remediation(load_json(args.input)))
        else:
            packet = verify_packet(load_json(args.packet))
            write_json({"valid": True, "packet_digest": packet["packet_digest"],
                        "generation": packet["generation"], "counts": packet["counts"],
                        "authority": packet["authority"]})
    except (FeedbackRemediationError, OSError) as exc:
        sys.stderr.write("feedback-remediation: %s\n" % exc)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
