# SPDX-License-Identifier: MIT
"""HMAC trust-root certification for reward-settlement evidence.

The sibling reward_settlement_ledger reconciles declared evidence. This module
answers the stronger question: which declared sources are independently bound
to a separately signed trusted-source registry? Caller-authored source labels,
digests, and refs cannot certify themselves.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

INPUT_SCHEMA = "bounty-concierge/reward-settlement-input/v1"
REGISTRY_SCHEMA = "bounty-concierge/reward-settlement-trusted-sources/v1"
CERT_SCHEMA = "bounty-concierge/reward-settlement-certification/v1"
MAX_BYTES = 4 * 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+\-]{0,239}$")
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
AUTHORITIES = frozenset({"REPOSITORY", "OFFICIAL_OFFER", "SPONSOR", "PROVIDER", "WALLET", "BANK", "OPERATOR_CAPTURE"})


class CertificationError(ValueError):
    pass


def _pairs(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise CertificationError(f"duplicate JSON key: {k}")
        out[k] = v
    return out


def loads_strict(raw: bytes) -> Any:
    if type(raw) is not bytes or len(raw) > MAX_BYTES or raw.startswith(b"\xef\xbb\xbf"):
        raise CertificationError("input must be bounded BOM-free bytes")
    try:
        text = raw.decode("utf-8", "strict")
        def reject_float(value: str) -> None:
            raise CertificationError(f"floating JSON number forbidden: {value}")
        def reject_constant(value: str) -> None:
            raise CertificationError(f"non-finite JSON number forbidden: {value}")
        return json.loads(text, object_pairs_hook=_pairs, parse_float=reject_float, parse_constant=reject_constant)
    except CertificationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CertificationError("invalid strict UTF-8 JSON") from exc


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise CertificationError("value is not canonical JSON") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _exact(value: Any, keys: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise CertificationError(f"{name} must have exact keys {sorted(keys)}")
    return value


def _timestamp(value: Any, name: str) -> tuple[str, datetime]:
    if type(value) is not str or TS.fullmatch(value) is None:
        raise CertificationError(f"{name} must be canonical UTC seconds")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise CertificationError(f"{name} is not a real UTC timestamp") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise CertificationError(f"{name} must be canonical UTC seconds")
    return value, parsed


def _source(value: Any, name: str) -> dict[str, str]:
    value = _exact(value, {"source_id", "source_ref", "source_sha256", "observed_at", "authority"}, name)
    for key in ("source_id", "source_ref", "observed_at"):
        if type(value[key]) is not str or TOKEN.fullmatch(value[key]) is None:
            raise CertificationError(f"{name}.{key} invalid")
    if type(value["source_sha256"]) is not str or HEX64.fullmatch(value["source_sha256"]) is None:
        raise CertificationError(f"{name}.source_sha256 invalid")
    if value["authority"] not in AUTHORITIES:
        raise CertificationError(f"{name}.authority invalid")
    return dict(value)


def verify_registry(registry: Any, key: bytes) -> tuple[dict[str, dict[str, str]], str]:
    if type(key) is not bytes or len(key) < 16:
        raise CertificationError("trust key must be at least 16 bytes")
    registry = _exact(registry, {"schema", "generated_at", "sources", "signature_hmac_sha256"}, "registry")
    if registry["schema"] != REGISTRY_SCHEMA:
        raise CertificationError("registry schema invalid")
    _, registry_generated_dt = _timestamp(registry["generated_at"], "registry.generated_at")
    signature = registry["signature_hmac_sha256"]
    if type(signature) is not str or HEX64.fullmatch(signature) is None:
        raise CertificationError("registry signature invalid")
    body = {k: registry[k] for k in ("schema", "generated_at", "sources")}
    expected = hmac.new(key, canonical(body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise CertificationError("trusted-source registry HMAC verification failed")
    if type(registry["sources"]) is not list:
        raise CertificationError("registry.sources must be a list")
    trusted: dict[str, dict[str, str]] = {}
    fingerprints: dict[tuple[str, str, str, str], str] = {}
    for i, raw in enumerate(registry["sources"]):
        src = _source(raw, f"registry.sources[{i}]")
        _, observed_dt = _timestamp(src["observed_at"], f"registry.sources[{i}].observed_at")
        if observed_dt > registry_generated_dt:
            raise CertificationError(f"registry.sources[{i}].observed_at is after registry.generated_at")
        sid = src["source_id"]
        if sid in trusted:
            raise CertificationError(f"duplicate trusted source_id: {sid}")
        fp = (src["source_ref"], src["source_sha256"], src["observed_at"], src["authority"])
        if fp in fingerprints:
            raise CertificationError(f"trusted source remint: {sid} duplicates {fingerprints[fp]}")
        trusted[sid] = src
        fingerprints[fp] = sid
    return trusted, hashlib.sha256(canonical(body)).hexdigest()


def _is_trusted(raw_source: Any, trusted: dict[str, dict[str, str]], name: str) -> tuple[dict[str, str], bool]:
    src = _source(raw_source, name)
    return src, trusted.get(src["source_id"]) == src


def _validate_declared_document(document: dict[str, Any]) -> None:
    # Reuse the landed ledger's strict schema/transition validator rather than
    # maintaining a second commercial-event parser. Certification then adds
    # the independent trust root on top of exactly the same accepted input.
    try:
        from .reward_settlement_ledger import LedgerInputError, compile_document
        compile_document(document)
    except LedgerInputError as exc:
        raise CertificationError(f"settlement input failed ledger validation: {exc}") from exc


def _trusted_transfer_finals(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(event["transfer_id"], []).append(event)
    finals = []
    for rows in grouped.values():
        rows = sorted(rows, key=lambda r: (r["source"]["observed_at"], r["event_id"]))
        finals.append(rows[-1])
    return finals


def certify(document: Any, registry: Any, key: bytes) -> dict[str, Any]:
    trusted, registry_body_sha = verify_registry(registry, key)
    document = _exact(document, {"schema", "generated_at", "cases"}, "input")
    if document["schema"] != INPUT_SCHEMA or type(document["cases"]) is not list:
        raise CertificationError("settlement input schema/cases invalid")
    _validate_declared_document(document)
    records = []
    bound_ids: set[str] = set()
    unbound_ids: set[str] = set()
    for index, case in enumerate(document["cases"]):
        if type(case) is not dict or set(case) != {"case_id", "work", "events"}:
            raise CertificationError(f"cases[{index}] invalid")
        case_id = case["case_id"]
        if type(case_id) is not str or TOKEN.fullmatch(case_id) is None:
            raise CertificationError(f"cases[{index}].case_id invalid")
        work = case["work"]
        if type(work) is not dict or "source" not in work:
            raise CertificationError(f"cases[{index}].work invalid")
        work_src, work_trusted = _is_trusted(work["source"], trusted, f"cases[{index}].work.source")
        (bound_ids if work_trusted else unbound_ids).add(work_src["source_id"])
        trusted_events = []
        for j, event in enumerate(case["events"]):
            if type(event) is not dict or not {"event_id", "kind", "source"} <= set(event):
                raise CertificationError(f"cases[{index}].events[{j}] invalid")
            src, ok = _is_trusted(event["source"], trusted, f"cases[{index}].events[{j}].source")
            (bound_ids if ok else unbound_ids).add(src["source_id"])
            if ok:
                trusted_events.append(event)
        kinds = {kind: [e for e in trusted_events if e["kind"] == kind] for kind in (
            "ADVERTISED_BOUNTY", "SPONSOR_AWARD", "ELIGIBILITY", "PAYOUT_TICKET", "PAYOUT_RAIL", "TRANSFER", "CLOSURE")}
        transfer_finals = _trusted_transfer_finals(kinds["TRANSFER"])
        confirmed = [e for e in transfer_finals if e.get("status") == "CONFIRMED" and e.get("direction") == "INCOMING"]
        closures = kinds["CLOSURE"]
        if confirmed and closures:
            raise CertificationError(f"{case_id}: trusted closure conflicts with trusted confirmed payment")
        if not work_trusted:
            state = "UNTRUSTED_WORK_SOURCE"
        elif closures:
            state = "CLOSED_WITHOUT_REWARD_CERTIFIED"
        elif confirmed:
            state = "PAID_CERTIFIED"
        elif transfer_finals:
            state = "TRANSFER_EVIDENCE_CERTIFIED"
        elif kinds["PAYOUT_RAIL"]:
            state = "PAYOUT_RAIL_CERTIFIED"
        elif kinds["PAYOUT_TICKET"]:
            state = "PAYOUT_TICKET_CERTIFIED"
        elif kinds["SPONSOR_AWARD"]:
            state = "SPONSOR_AWARD_CERTIFIED"
        elif kinds["ADVERTISED_BOUNTY"]:
            state = "ADVERTISED_CERTIFIED"
        else:
            state = "MERGE_ONLY_CERTIFIED" if work_trusted else "UNTRUSTED_WORK_SOURCE"
        paid: dict[str, int] = {}
        for event in confirmed:
            amount, currency = event.get("amount_minor"), event.get("currency")
            if type(amount) is not int or amount <= 0 or type(currency) is not str:
                raise CertificationError(f"{case_id}: confirmed transfer money fields invalid")
            paid[currency] = paid.get(currency, 0) + amount
        records.append({
            "case_id": case_id,
            "certified_settlement_state": state,
            "work_source_certified": work_trusted,
            "certified_paid_by_currency": dict(sorted(paid.items())),
            "trusted_event_count": len(trusted_events),
            "declared_event_count": len(case["events"]),
            "certified_source_ids": sorted({work_src["source_id"]} | {e["source"]["source_id"] for e in trusted_events}) if work_trusted else sorted({e["source"]["source_id"] for e in trusted_events}),
            "truth": {
                "merge_proves_payment": False,
                "caller_declared_authority_proves_trust": False,
                "unsigned_registry_proves_trust": False,
                "certified_paid": state == "PAID_CERTIFIED",
                "recognized_revenue": False,
            },
        })
    records.sort(key=lambda r: r["case_id"])
    paid_total: dict[str, int] = {}
    for record in records:
        for cur, amount in record["certified_paid_by_currency"].items():
            paid_total[cur] = paid_total.get(cur, 0) + amount
    body = {
        "schema": CERT_SCHEMA,
        "input_generated_at": document["generated_at"],
        "trusted_registry_generated_at": registry["generated_at"],
        "trusted_registry_body_sha256": registry_body_sha,
        "records": records,
        "aggregates": {
            "case_count": len(records),
            "paid_certified_by_currency": dict(sorted(paid_total.items())),
            "bound_source_ids": sorted(bound_ids),
            "unbound_source_ids": sorted(unbound_ids - bound_ids),
        },
        "authority": {
            "send_outbound": False,
            "request_payout": False,
            "mutate_provider": False,
            "mutate_wallet_or_bank": False,
            "recognize_accounting_revenue": False,
        },
    }
    return {**body, "receipt_sha256": _sha(body)}


def verify_certificate(document: Any, registry: Any, key: bytes, certificate: Any) -> bool:
    try:
        rebuilt = certify(document, registry, key)
    except CertificationError:
        return False
    return hmac.compare_digest(canonical(rebuilt), canonical(certificate))


def render_markdown(certificate: dict[str, Any]) -> str:
    lines = ["# Reward settlement certification", "", "Only sources bound by the signed out-of-band registry can certify settlement facts.", ""]
    for row in certificate["records"]:
        paid = ", ".join(f"{c} {a} minor units" for c, a in row["certified_paid_by_currency"].items()) or "none"
        lines += [f"## {row['case_id']}", f"- State: `{row['certified_settlement_state']}`", f"- Certified paid: {paid}", ""]
    lines += ["## Authority ceiling", "", "No outbound, payout, provider/wallet mutation, or accounting revenue authority is granted.", ""]
    return "\n".join(lines)


def _read(path: Path) -> Any:
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode) or st.st_size > MAX_BYTES:
        raise CertificationError("input must be a bounded regular non-symlink file")
    return loads_strict(path.read_bytes())


def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags, 0o600)
    except OSError as exc:
        raise CertificationError("output must not already exist and must not be a symlink") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True, type=Path); p.add_argument("--registry", required=True, type=Path)
    p.add_argument("--certificate", required=True, type=Path); p.add_argument("--markdown", type=Path)
    p.add_argument("--key-env", default="REWARD_SETTLEMENT_TRUST_KEY")
    ns = p.parse_args(argv)
    try:
        key_text = os.environ.get(ns.key_env)
        if not key_text:
            raise CertificationError(f"missing trust key environment variable: {ns.key_env}")
        cert = certify(_read(ns.input), _read(ns.registry), key_text.encode("utf-8"))
        _write_exclusive(ns.certificate, canonical(cert) + b"\n")
        if ns.markdown:
            try:
                _write_exclusive(ns.markdown, render_markdown(cert).encode("utf-8"))
            except Exception:
                try:
                    ns.certificate.unlink()
                except OSError:
                    pass
                raise
        print(cert["receipt_sha256"])
        return 0
    except (OSError, CertificationError) as exc:
        p.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
