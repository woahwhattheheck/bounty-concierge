# SPDX-License-Identifier: MIT
"""Deterministic owner-review queue for independently certified reward settlement.

This module composes the landed reward settlement ledger and trust-root certifier.
It never treats a merge, advertised bounty, payout ticket, payout rail, or
non-terminal transfer as cash. Non-terminal cases are routed to an internal
owner-review queue only after the certificate is re-verified against the signed
trusted-source registry.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable, Optional

QUEUE_SCHEMA = "bounty-concierge/certified-settlement-collections-queue/v1"
RECEIPT_SCHEMA = "bounty-concierge/certified-settlement-collections-queue-receipt/v1"
CONTACT_SCHEMA = "bounty-concierge/settlement-contact-routes/v1"
MAX_BYTES = 4 * 1024 * 1024
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+\-]{0,239}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ROUTE_CHANNELS = frozenset({"EMAIL", "GITHUB_ISSUE", "GITHUB_PR", "FORM", "PORTAL", "OTHER"})
QUEUE_STATES = frozenset({
    "SETTLED", "CLOSED_NO_REWARD", "NEEDS_TRUST_EVIDENCE",
    "AWARD_FOLLOWUP_CANDIDATE", "PAYOUT_TICKET_FOLLOWUP_CANDIDATE",
    "PAYOUT_RAIL_FOLLOWUP_CANDIDATE", "TRANSFER_PENDING_FOLLOWUP_CANDIDATE",
    "HOLD_CONTRADICTION",
})
CERT_RANK = {
    "UNTRUSTED_WORK_SOURCE": -1, "MERGE_ONLY_CERTIFIED": 0,
    "ADVERTISED_CERTIFIED": 1, "SPONSOR_AWARD_CERTIFIED": 2,
    "PAYOUT_TICKET_CERTIFIED": 3, "PAYOUT_RAIL_CERTIFIED": 4,
    "TRANSFER_EVIDENCE_CERTIFIED": 5, "PAID_CERTIFIED": 6,
}
FOLLOWUP_STATE_BY_RANK = {
    1: "AWARD_FOLLOWUP_CANDIDATE", 2: "PAYOUT_TICKET_FOLLOWUP_CANDIDATE",
    3: "PAYOUT_RAIL_FOLLOWUP_CANDIDATE", 4: "TRANSFER_PENDING_FOLLOWUP_CANDIDATE",
    5: "TRANSFER_PENDING_FOLLOWUP_CANDIDATE",
}
PROVIDER_AUTHORITIES = frozenset({"SPONSOR", "PROVIDER", "WALLET", "BANK"})


class CollectionsQueueError(ValueError):
    """Malformed, stale, contradictory, or unverifiable queue input."""


class CollectionsQueueVerificationError(RuntimeError):
    """Published queue bytes do not match deterministic recomputation."""


def _pairs(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise CollectionsQueueError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def loads_strict(raw: bytes) -> Any:
    if type(raw) is not bytes or len(raw) > MAX_BYTES or raw.startswith(b"\xef\xbb\xbf"):
        raise CollectionsQueueError("input must be bounded BOM-free bytes")
    try:
        text = raw.decode("utf-8", "strict")
        def reject_float(value: str) -> None:
            raise CollectionsQueueError(f"floating JSON number forbidden: {value}")
        def reject_constant(value: str) -> None:
            raise CollectionsQueueError(f"non-finite JSON number forbidden: {value}")
        return json.loads(text, object_pairs_hook=_pairs, parse_float=reject_float, parse_constant=reject_constant)
    except CollectionsQueueError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectionsQueueError("invalid strict UTF-8 JSON") from exc


def canonical(value: Any) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError) as exc:
        raise CollectionsQueueError("value is not canonical JSON") from exc


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _exact(value: Any, keys: set[str], field: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise CollectionsQueueError(f"{field} must have exact keys {sorted(keys)}")
    return value


def _token(value: Any, field: str) -> str:
    if type(value) is not str or TOKEN.fullmatch(value) is None:
        raise CollectionsQueueError(f"{field} must be a bounded token")
    return value


def _timestamp(value: Any, field: str) -> tuple[str, datetime]:
    if type(value) is not str or TS.fullmatch(value) is None:
        raise CollectionsQueueError(f"{field} must be canonical UTC seconds")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise CollectionsQueueError(f"{field} is not a real UTC timestamp") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise CollectionsQueueError(f"{field} must be canonical UTC seconds")
    return value, parsed


def _positive_seconds(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0 or value > 366 * 24 * 60 * 60:
        raise CollectionsQueueError(f"{field} must be a positive bounded integer")
    return value


def _source_observation(raw_source: Any, field: str) -> tuple[str, str, datetime]:
    if type(raw_source) is not dict:
        raise CollectionsQueueError(f"{field} must be an object")
    source_id = _token(raw_source.get("source_id"), f"{field}.source_id")
    authority = raw_source.get("authority")
    if type(authority) is not str:
        raise CollectionsQueueError(f"{field}.authority invalid")
    _, observed_dt = _timestamp(raw_source.get("observed_at"), f"{field}.observed_at")
    return source_id, authority, observed_dt


def _declared_rank(row: dict[str, Any]) -> int:
    rank = 0
    if row.get("advertised") is not None: rank = 1
    if row.get("award") is not None: rank = 2
    if row.get("payout_ticket", {}).get("ticket_ids"): rank = 3
    if row.get("payout_rail", {}).get("rail_refs"): rank = 4
    if row.get("transfers"): rank = 5
    if row.get("paid_confirmed_by_currency"): rank = 6
    return rank


def _trusted_event_facts(raw_case: dict[str, Any], cert_record: dict[str, Any], *, as_of_dt: datetime) -> dict[str, Any]:
    certified_ids = set(cert_record.get("certified_source_ids", []))
    latest_provider_dt: Optional[datetime] = None
    eligibility: set[str] = set()
    trusted_ids: list[str] = []
    trusted_kinds: set[str] = set()
    for index, event in enumerate(raw_case["events"]):
        sid, authority, observed_dt = _source_observation(event.get("source"), f"case[{raw_case['case_id']}].events[{index}].source")
        if sid not in certified_ids:
            continue
        if observed_dt > as_of_dt:
            raise CollectionsQueueError(f"{raw_case['case_id']}: certified event {event.get('event_id')} is after as_of")
        trusted_ids.append(_token(event.get("event_id"), "event.event_id"))
        trusted_kinds.add(_token(event.get("kind"), "event.kind"))
        if authority in PROVIDER_AUTHORITIES:
            latest_provider_dt = observed_dt if latest_provider_dt is None else max(latest_provider_dt, observed_dt)
        if event.get("kind") == "ELIGIBILITY":
            decision = event.get("decision")
            if decision not in {"ELIGIBLE", "INELIGIBLE"}:
                raise CollectionsQueueError(f"{raw_case['case_id']}: certified eligibility decision invalid")
            eligibility.add(decision)
    if len(eligibility) > 1:
        raise CollectionsQueueError(f"{raw_case['case_id']}: certified eligibility contradiction")
    return {
        "trusted_event_ids": sorted(trusted_ids),
        "trusted_event_kinds": sorted(trusted_kinds),
        "certified_eligibility": next(iter(eligibility)) if eligibility else None,
        "last_certified_provider_event_at": latest_provider_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if latest_provider_dt else None,
        "age_since_last_certified_provider_event_seconds": int((as_of_dt - latest_provider_dt).total_seconds()) if latest_provider_dt else None,
    }


def _compile_contact_routes(raw: Any, *, as_of_dt: datetime) -> dict[str, list[dict[str, Any]]]:
    if raw is None:
        return {}
    root = _exact(raw, {"schema", "generated_at", "routes"}, "contact_routes")
    if root["schema"] != CONTACT_SCHEMA:
        raise CollectionsQueueError(f"contact_routes.schema must be {CONTACT_SCHEMA}")
    _, generated_dt = _timestamp(root["generated_at"], "contact_routes.generated_at")
    if generated_dt > as_of_dt:
        raise CollectionsQueueError("contact_routes.generated_at may not be after as_of")
    routes = root["routes"]
    if type(routes) is not list or len(routes) > 10_000:
        raise CollectionsQueueError("contact_routes.routes must be a bounded list")
    ids: set[str] = set(); fingerprints: set[tuple[str, str, str]] = set(); by_case: dict[str, list[dict[str, Any]]] = {}
    expected = {"case_id", "route_id", "channel", "target_ref", "source_ref", "source_sha256", "observed_at", "valid_until"}
    for i, raw_route in enumerate(routes):
        route = _exact(raw_route, expected, f"contact_routes.routes[{i}]")
        case_id = _token(route["case_id"], f"routes[{i}].case_id")
        route_id = _token(route["route_id"], f"routes[{i}].route_id")
        if route_id in ids: raise CollectionsQueueError(f"duplicate contact route_id: {route_id}")
        ids.add(route_id)
        channel = route["channel"]
        if type(channel) is not str or channel not in ROUTE_CHANNELS: raise CollectionsQueueError(f"contact route {route_id}: unsupported channel")
        target = _token(route["target_ref"], f"route {route_id}.target_ref"); source_ref = _token(route["source_ref"], f"route {route_id}.source_ref")
        sha = route["source_sha256"]
        if type(sha) is not str or HEX64.fullmatch(sha) is None: raise CollectionsQueueError(f"contact route {route_id}: source_sha256 invalid")
        observed_at, observed_dt = _timestamp(route["observed_at"], f"route {route_id}.observed_at")
        if observed_dt > as_of_dt: raise CollectionsQueueError(f"contact route {route_id}: observed_at is after as_of")
        valid_until = route["valid_until"]; valid_dt: Optional[datetime] = None
        if valid_until is not None:
            valid_until, valid_dt = _timestamp(valid_until, f"route {route_id}.valid_until")
            if valid_dt < observed_dt: raise CollectionsQueueError(f"contact route {route_id}: valid_until predates observed_at")
        fp = (channel, target, source_ref)
        if fp in fingerprints: raise CollectionsQueueError(f"contact route remint detected: {route_id}")
        fingerprints.add(fp)
        by_case.setdefault(case_id, []).append({
            "route_id": route_id, "channel": channel, "target_ref": target,
            "source_ref": source_ref, "source_sha256": sha, "observed_at": observed_at,
            "valid_until": valid_until, "current": valid_dt is None or valid_dt >= as_of_dt,
        })
    return {case_id: sorted(values, key=lambda x: x["route_id"]) for case_id, values in by_case.items()}


def _classify(ledger: dict[str, Any], cert: dict[str, Any], *, source_current: bool, facts: dict[str, Any]) -> tuple[str, str]:
    cert_state = cert.get("certified_settlement_state"); ledger_state = ledger.get("settlement_state")
    if cert_state == "PAID_CERTIFIED":
        return ("SETTLED", "terminal confirmed incoming payment is independently certified") if ledger_state == "PAID_CONFIRMED" else ("HOLD_CONTRADICTION", "certified paid state disagrees with deterministic ledger")
    if cert_state == "CLOSED_WITHOUT_REWARD_CERTIFIED":
        return ("CLOSED_NO_REWARD", "terminal no-reward closure is independently certified") if ledger_state == "CLOSED_WITHOUT_REWARD" else ("HOLD_CONTRADICTION", "certified closure disagrees with deterministic ledger")
    if cert_state == "UNTRUSTED_WORK_SOURCE":
        return "NEEDS_TRUST_EVIDENCE", "merged work source is not independently certified"
    rank = CERT_RANK.get(cert_state)
    if rank is None: return "HOLD_CONTRADICTION", f"unsupported certification state: {cert_state}"
    declared = _declared_rank(ledger)
    if declared > rank: return "NEEDS_TRUST_EVIDENCE", "declared settlement evidence is stronger than the independently certified chain"
    if rank > declared: return "HOLD_CONTRADICTION", "certified settlement chain outruns deterministic ledger evidence"
    if facts["certified_eligibility"] == "INELIGIBLE": return "HOLD_CONTRADICTION", "independently certified eligibility says INELIGIBLE"
    if rank >= 2 and rank < 6 and "SPONSOR_AWARD" not in facts["trusted_event_kinds"]:
        return "NEEDS_TRUST_EVIDENCE", "nonterminal payout chain lacks independently certified sponsor-award evidence"
    if not source_current: return "NEEDS_TRUST_EVIDENCE", "settlement input or trust registry generation is stale at as_of"
    if rank == 0: return "NEEDS_TRUST_EVIDENCE", "merge is certified but no independently certified money road exists"
    followup = FOLLOWUP_STATE_BY_RANK.get(rank)
    return (followup, "next evidence step is missing from the certified settlement chain") if followup else ("HOLD_CONTRADICTION", "certification state has no safe queue transition")


def compile_queue(document: Any, registry: Any, certificate: Any, *, key: bytes, as_of: str, max_evidence_age_seconds: int, contact_routes: Any = None, raw_bindings: Optional[dict[str, Optional[str]]] = None) -> dict[str, Any]:
    max_age = _positive_seconds(max_evidence_age_seconds, "max_evidence_age_seconds"); as_of, as_of_dt = _timestamp(as_of, "as_of")
    try:
        from .reward_settlement_certifier import CertificationError, verify_certificate
        from .reward_settlement_ledger import LedgerInputError, compile_document
    except ImportError as exc:
        raise CollectionsQueueError("landed settlement ledger/certifier modules are unavailable") from exc
    try:
        if not verify_certificate(document, registry, key, certificate): raise CollectionsQueueError("certificate does not verify against input + signed registry")
        ledger = compile_document(document)
    except (CertificationError, LedgerInputError) as exc:
        raise CollectionsQueueError(f"upstream settlement verification failed: {exc}") from exc
    if type(certificate) is not dict: raise CollectionsQueueError("certificate must be an object")
    input_at, input_dt = _timestamp(certificate.get("input_generated_at"), "certificate.input_generated_at")
    registry_at, registry_dt = _timestamp(certificate.get("trusted_registry_generated_at"), "certificate.trusted_registry_generated_at")
    if input_at != document.get("generated_at"): raise CollectionsQueueError("certificate input generation does not match settlement input")
    if as_of_dt < input_dt or as_of_dt < registry_dt: raise CollectionsQueueError("as_of may not predate settlement/certification generations")
    source_age = max(int((as_of_dt - input_dt).total_seconds()), int((as_of_dt - registry_dt).total_seconds())); source_current = source_age <= max_age
    ledger_rows = {r["case_id"]: r for r in ledger["records"]}; cert_rows = {r["case_id"]: r for r in certificate.get("records", [])}; raw_cases = {r["case_id"]: r for r in document.get("cases", [])}
    if set(ledger_rows) != set(cert_rows) or set(ledger_rows) != set(raw_cases): raise CollectionsQueueError("ledger, certificate, and settlement case sets differ")
    routes = _compile_contact_routes(contact_routes, as_of_dt=as_of_dt)
    unknown = sorted(set(routes) - set(raw_cases))
    if unknown: raise CollectionsQueueError(f"contact routes reference unknown cases: {unknown}")
    records = []
    for case_id in sorted(raw_cases):
        led = ledger_rows[case_id]; cert = cert_rows[case_id]; facts = _trusted_event_facts(raw_cases[case_id], cert, as_of_dt=as_of_dt)
        state, reason = _classify(led, cert, source_current=source_current, facts=facts)
        route_rows = routes.get(case_id, []); current_routes = [r for r in route_rows if r["current"]]
        route_state = "CURRENT" if current_routes else ("STALE" if route_rows else "MISSING")
        award = led.get("award"); advertised = led.get("advertised"); certified_ids = set(cert.get("certified_source_ids", []))
        award_certified = bool(award and set(award.get("source_ids", [])) <= certified_ids)
        advertised_certified = bool(advertised and set(advertised.get("source_ids", [])) <= certified_ids)
        records.append({
            "case_id": case_id, "work": led["work"], "queue_state": state, "reason": reason,
            "certified_settlement_state": cert["certified_settlement_state"], "declared_settlement_state": led["settlement_state"],
            "certified_eligibility": facts["certified_eligibility"],
            "commercial_facts": {
                "advertised": advertised, "advertised_certified": advertised_certified,
                "sponsor_award": award, "sponsor_award_certified": award_certified,
                "certified_paid_by_currency": cert.get("certified_paid_by_currency", {}), "conversion_performed": False,
            },
            "evidence": {
                "source_current": source_current, "source_age_seconds": source_age,
                "last_certified_provider_event_at": facts["last_certified_provider_event_at"],
                "age_since_last_certified_provider_event_seconds": facts["age_since_last_certified_provider_event_seconds"],
                "certified_source_ids": cert.get("certified_source_ids", []), "trusted_event_ids": facts["trusted_event_ids"],
            },
            "contact_routes": {
                "state": route_state, "routes": route_rows, "current_route_ids": [r["route_id"] for r in current_routes],
                "route_presence_grants_send_authority": False,
            },
            "authority": {"owner_review_only": True, "send_outbound": False, "request_payout": False, "mutate_provider": False, "mutate_wallet_or_bank": False, "recognize_accounting_revenue": False},
        })
    counts: dict[str, int] = {}
    for row in records: counts[row["queue_state"]] = counts.get(row["queue_state"], 0) + 1
    bindings = raw_bindings or {}
    return {
        "schema": QUEUE_SCHEMA, "as_of": as_of, "max_evidence_age_seconds": max_age,
        "settlement_input_generated_at": input_at, "trusted_registry_generated_at": registry_at,
        "trusted_registry_body_sha256": certificate.get("trusted_registry_body_sha256"), "certification_receipt_sha256": certificate.get("receipt_sha256"),
        "input_bindings": {
            "settlement_input_sha256": bindings.get("settlement_input_sha256"), "trusted_registry_sha256": bindings.get("trusted_registry_sha256"),
            "certificate_sha256": bindings.get("certificate_sha256"), "contact_routes_sha256": bindings.get("contact_routes_sha256"),
        },
        "records": records,
        "aggregates": {"case_count": len(records), "state_counts": dict(sorted(counts.items())), "settled_certified_by_currency": certificate.get("aggregates", {}).get("paid_certified_by_currency", {}), "recognized_revenue_by_currency": {}},
        "authority": {"owner_review_only": True, "send_outbound": False, "request_payout": False, "mutate_provider": False, "mutate_wallet_or_bank": False, "recognize_accounting_revenue": False},
    }


def render_markdown(queue: dict[str, Any]) -> str:
    lines = ["# Certified settlement collections queue", "", f"As of: `{queue['as_of']}`", "", "> Internal owner-review queue only. Merge is not award or payment. Advertised is not awarded. Ticket/rail is not transfer. Noncash units are never silently converted. Route evidence is not send authority.", "", "## Queue", ""]
    for row in queue["records"]:
        work = row["work"]
        lines += [f"### {row['case_id']} — {work['repo']}#{work['pr']}", "", f"- Queue state: `{row['queue_state']}`", f"- Certified settlement: `{row['certified_settlement_state']}`", f"- Reason: {row['reason']}", f"- Contact route evidence: `{row['contact_routes']['state']}`", f"- Last certified provider event: `{row['evidence']['last_certified_provider_event_at'] or 'none'}`", ""]
    lines += ["## Authority ceiling", "", "This queue cannot send, request payout, mutate a provider/wallet/bank, recognize accounting revenue, or convert noncash units. A later outbound action requires its own fresh single-writer/DNR controls.", ""]
    return "\n".join(lines)


def compile_bytes(settlement_raw: bytes, registry_raw: bytes, certificate_raw: bytes, *, key: bytes, as_of: str, max_evidence_age_seconds: int, contact_routes_raw: Optional[bytes] = None) -> tuple[bytes, bytes, bytes]:
    document = loads_strict(settlement_raw); registry = loads_strict(registry_raw); certificate = loads_strict(certificate_raw); routes = loads_strict(contact_routes_raw) if contact_routes_raw is not None else None
    bindings = {"settlement_input_sha256": _sha(settlement_raw), "trusted_registry_sha256": _sha(registry_raw), "certificate_sha256": _sha(certificate_raw), "contact_routes_sha256": _sha(contact_routes_raw) if contact_routes_raw is not None else None}
    queue = compile_queue(document, registry, certificate, key=key, as_of=as_of, max_evidence_age_seconds=max_evidence_age_seconds, contact_routes=routes, raw_bindings=bindings)
    queue_raw = canonical(queue); markdown_raw = render_markdown(queue).encode("utf-8")
    receipt = {
        "schema": RECEIPT_SCHEMA, "as_of": as_of, "max_evidence_age_seconds": max_evidence_age_seconds,
        **bindings, "queue_sha256": _sha(queue_raw), "markdown_sha256": _sha(markdown_raw),
        "certification_receipt_sha256": queue["certification_receipt_sha256"], "record_count": len(queue["records"]), "authority": queue["authority"],
    }
    return queue_raw, markdown_raw, canonical(receipt)


def verify_bytes(settlement_raw: bytes, registry_raw: bytes, certificate_raw: bytes, queue_raw: bytes, markdown_raw: bytes, receipt_raw: bytes, *, key: bytes, as_of: str, max_evidence_age_seconds: int, contact_routes_raw: Optional[bytes] = None) -> None:
    expected = compile_bytes(settlement_raw, registry_raw, certificate_raw, key=key, as_of=as_of, max_evidence_age_seconds=max_evidence_age_seconds, contact_routes_raw=contact_routes_raw)
    if (queue_raw, markdown_raw, receipt_raw) != expected: raise CollectionsQueueVerificationError("queue artifacts do not match deterministic recomputation")


def _read_stable(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try: fd = os.open(str(path), flags)
    except OSError as exc: raise CollectionsQueueError(f"cannot open bounded regular input: {path}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES: raise CollectionsQueueError(f"input must be a bounded regular file: {path}")
        chunks = []; total = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk: break
            total += len(chunk)
            if total > MAX_BYTES: raise CollectionsQueueError(f"input exceeds maximum size: {path}")
            chunks.append(chunk)
        after = os.fstat(fd); raw = b"".join(chunks)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or len(raw) != before.st_size:
            raise CollectionsQueueError(f"input changed during read: {path}")
        return raw
    finally: os.close(fd)


def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try: fd = os.open(str(path), flags, 0o600)
    except OSError as exc: raise CollectionsQueueError(f"output must not already exist: {path}") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
    except Exception:
        try: path.unlink()
        except OSError: pass
        raise


def _trust_key(env_name: str) -> bytes:
    value = os.environ.get(env_name)
    if value is None or len(value.encode("utf-8")) < 16: raise CollectionsQueueError(f"missing/short trust key environment variable: {env_name}")
    return value.encode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="command", required=True)
    for name in ("compile", "verify"):
        cmd = sub.add_parser(name); cmd.add_argument("--input", required=True, type=Path); cmd.add_argument("--registry", required=True, type=Path); cmd.add_argument("--certificate", required=True, type=Path); cmd.add_argument("--contact-routes", type=Path); cmd.add_argument("--as-of", required=True); cmd.add_argument("--max-evidence-age-seconds", required=True, type=int); cmd.add_argument("--key-env", default="REWARD_SETTLEMENT_TRUST_KEY")
        if name == "compile": cmd.add_argument("--out-dir", required=True, type=Path)
        else: cmd.add_argument("--queue", required=True, type=Path); cmd.add_argument("--markdown", required=True, type=Path); cmd.add_argument("--receipt", required=True, type=Path)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    ns = build_parser().parse_args(argv)
    try:
        settlement_raw = _read_stable(ns.input); registry_raw = _read_stable(ns.registry); certificate_raw = _read_stable(ns.certificate); routes_raw = _read_stable(ns.contact_routes) if ns.contact_routes else None; key = _trust_key(ns.key_env)
        kwargs = {"key": key, "as_of": ns.as_of, "max_evidence_age_seconds": ns.max_evidence_age_seconds, "contact_routes_raw": routes_raw}
        if ns.command == "compile":
            queue_raw, markdown_raw, receipt_raw = compile_bytes(settlement_raw, registry_raw, certificate_raw, **kwargs); ns.out_dir.mkdir(parents=True, exist_ok=False)
            try:
                _write_exclusive(ns.out_dir / "queue.json", queue_raw); _write_exclusive(ns.out_dir / "queue.md", markdown_raw); _write_exclusive(ns.out_dir / "receipt.json", receipt_raw)
            except Exception:
                for name in ("queue.json", "queue.md", "receipt.json"):
                    try: (ns.out_dir / name).unlink()
                    except OSError: pass
                try: ns.out_dir.rmdir()
                except OSError: pass
                raise
            return 0
        verify_bytes(settlement_raw, registry_raw, certificate_raw, _read_stable(ns.queue), _read_stable(ns.markdown), _read_stable(ns.receipt), **kwargs); return 0
    except (OSError, CollectionsQueueError, CollectionsQueueVerificationError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
