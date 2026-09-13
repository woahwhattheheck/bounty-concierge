# SPDX-License-Identifier: MIT
"""Evidence-bound gate for speculative/unpaid work with a concrete payoff path.

The gate answers a deliberately narrow owner question: should another unpaid unit of
work be considered when compensation is not yet secured?  It never estimates win
probability, invents monetary value, authorizes outreach/submission, or recognizes
cash/revenue.  READY means only that an owner has a current, evidence-bound path to
review within a bounded free-work budget.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

WORK_SCHEMA = "payoff-path-work/v1"
PACKET_SCHEMA = "payoff-path-gate/v1"
RECEIPT_SCHEMA = "payoff-path-gate-receipt/v1"

MECHANISMS = {
    "BOUNTY",
    "COMPETITION_PRIZE",
    "PAID_OFFER_OR_PILOT",
    "PRIME_SUBCONTRACT",
    "REFERRAL_COMMISSION",
    "SPONSOR_OR_GRANT",
}
EVENT_FOR_MECHANISM = {
    "BOUNTY": "SUBMIT_WORK",
    "COMPETITION_PRIZE": "ENTER_COMPETITION",
    "PAID_OFFER_OR_PILOT": "SEND_PAID_OFFER",
    "PRIME_SUBCONTRACT": "SECURE_TEAMING",
    "REFERRAL_COMMISSION": "COMPLETE_REFERRAL",
    "SPONSOR_OR_GRANT": "APPLY_FOR_GRANT",
}
VALUE_KINDS = {"FIXED", "POOL", "NEGOTIATED", "UNSPECIFIED_BY_SOURCE"}
STATES = {
    "READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW",
    "HOLD_NO_PAYOFF_PATH",
    "HOLD_STALE_OR_INVALID",
    "STOP_UNPAID_WORK",
}

_MAX_INPUT_BYTES = 4_000_000
_MAX_ITEMS = 10_000
_MAX_ID = 128
_MAX_URL = 2048
_MAX_FREE_MINUTES = 10_000_000
_MAX_SOURCE_AGE_DAYS = 3650
_SAFE_INT = 9_007_199_254_740_991
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_SENSITIVE_REF = re.compile(
    r"(?:^|[-_./:])(email|phone|password|passwd|secret|token|bearer|api[-_]?key|private[-_]?key)(?:$|[-_./:])",
    re.IGNORECASE,
)


class PayoffPathError(ValueError):
    """Raised when payoff-path evidence is structurally unsafe or unverifiable."""


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PayoffPathError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_strict_json(text: str) -> Any:
    if type(text) is not str or len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
        raise PayoffPathError("JSON input is missing or too large")
    try:
        return json.loads(
            text,
            object_pairs_hook=_pairs_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                PayoffPathError(f"non-finite JSON constant: {value}")
            ),
        )
    except PayoffPathError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PayoffPathError("invalid JSON") from exc


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _exact_keys(value: Any, expected: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise PayoffPathError(f"{name} must be an object")
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise PayoffPathError(f"{name} keys mismatch; missing={missing} extra={extra}")
    return value


def _text(value: Any, name: str, *, pattern: re.Pattern[str] | None = None, limit: int = _MAX_ID) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > limit:
        raise PayoffPathError(f"{name} must be a bounded non-empty canonical string")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise PayoffPathError(f"{name} contains control characters")
    if pattern is not None and not pattern.fullmatch(value):
        raise PayoffPathError(f"{name} has invalid format")
    return value


def _opaque_ref(value: Any, name: str) -> str:
    ref = _text(value, name, pattern=_ID)
    if "@" in ref or _SENSITIVE_REF.search(ref):
        raise PayoffPathError(f"{name} must not encode contact details or secrets")
    return ref


def _sha(value: Any, name: str) -> str:
    return _text(value, name, pattern=_HEX64, limit=64)


def _int(value: Any, name: str, *, minimum: int = 0, maximum: int = _SAFE_INT) -> int:
    if type(value) is not int:
        raise PayoffPathError(f"{name} must be an integer (bool is not accepted)")
    if value < minimum or value > maximum:
        raise PayoffPathError(f"{name} must be between {minimum} and {maximum}")
    return value


def _timestamp(value: Any, name: str) -> datetime:
    text = _text(value, name, limit=24)
    if not _UTC.fullmatch(text):
        raise PayoffPathError(f"{name} must be canonical millisecond UTC")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PayoffPathError(f"{name} is invalid") from exc
    if _render_timestamp(parsed) != text:
        raise PayoffPathError(f"{name} must be canonical millisecond UTC")
    return parsed


def _render_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise PayoffPathError("trusted time must be timezone-aware")
    utc = value.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _trusted_now(value: datetime | str | None) -> datetime:
    if value is None:
        value = datetime.now(timezone.utc)
    if type(value) is str:
        return _timestamp(value, "trusted_as_of")
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PayoffPathError("trusted_as_of must be timezone-aware datetime or canonical UTC string")
    rendered = _render_timestamp(value)
    return _timestamp(rendered, "trusted_as_of")


def _https_url(value: Any, name: str) -> str:
    text = _text(value, name, limit=_MAX_URL)
    if "`" in text or "<" in text or ">" in text:
        raise PayoffPathError(f"{name} contains unsafe markup characters")
    parts = urlsplit(text)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password or parts.fragment:
        raise PayoffPathError(f"{name} must be an HTTPS URL without credentials or fragment")
    if parts.hostname is None:
        raise PayoffPathError(f"{name} must have a hostname")
    return text


def _normalize_value(raw: Any, name: str) -> dict[str, Any]:
    value = _exact_keys(raw, {"kind", "currency", "amount_minor"}, name)
    kind = _text(value["kind"], f"{name}.kind")
    if kind not in VALUE_KINDS:
        raise PayoffPathError(f"{name}.kind is unsupported")

    currency = value["currency"]
    amount = value["amount_minor"]
    if kind in {"FIXED", "POOL"}:
        currency = _text(currency, f"{name}.currency", pattern=_CURRENCY, limit=3)
        amount = _int(amount, f"{name}.amount_minor", minimum=1)
    else:
        if currency is not None or amount is not None:
            raise PayoffPathError(
                f"{name} cannot attach invented numeric value to {kind}; currency and amount_minor must be null"
            )
        currency = None
        amount = None
    return {"kind": kind, "currency": currency, "amount_minor": amount}


def _normalize_source(raw: Any, name: str) -> dict[str, Any]:
    source = _exact_keys(
        raw,
        {"canonical_url", "evidence_ref", "evidence_sha256", "observed_at_utc", "max_age_days"},
        name,
    )
    return {
        "canonical_url": _https_url(source["canonical_url"], f"{name}.canonical_url"),
        "evidence_ref": _opaque_ref(source["evidence_ref"], f"{name}.evidence_ref"),
        "evidence_sha256": _sha(source["evidence_sha256"], f"{name}.evidence_sha256"),
        "observed_at_utc": _render_timestamp(_timestamp(source["observed_at_utc"], f"{name}.observed_at_utc")),
        "max_age_days": _int(source["max_age_days"], f"{name}.max_age_days", minimum=1, maximum=_MAX_SOURCE_AGE_DAYS),
    }


def _normalize_conversion(raw: Any, name: str, mechanism: str) -> dict[str, Any]:
    conversion = _exact_keys(raw, {"event", "due_at_utc", "evidence_ref", "evidence_sha256"}, name)
    event = _text(conversion["event"], f"{name}.event")
    expected = EVENT_FOR_MECHANISM[mechanism]
    if event != expected:
        raise PayoffPathError(f"{name}.event {event!r} does not match {mechanism}; expected {expected!r}")
    return {
        "event": event,
        "due_at_utc": _render_timestamp(_timestamp(conversion["due_at_utc"], f"{name}.due_at_utc")),
        "evidence_ref": _opaque_ref(conversion["evidence_ref"], f"{name}.evidence_ref"),
        "evidence_sha256": _sha(conversion["evidence_sha256"], f"{name}.evidence_sha256"),
    }


def _normalize_path(raw: Any, name: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    path = _exact_keys(raw, {"mechanism", "value", "source", "conversion"}, name)
    mechanism = _text(path["mechanism"], f"{name}.mechanism")
    if mechanism not in MECHANISMS:
        raise PayoffPathError(f"{name}.mechanism is unsupported")
    normalized_source = _normalize_source(path["source"], f"{name}.source")
    normalized_conversion = _normalize_conversion(path["conversion"], f"{name}.conversion", mechanism)
    return {
        "mechanism": mechanism,
        "value": _normalize_value(path["value"], f"{name}.value"),
        "source": normalized_source,
        "conversion": normalized_conversion,
    }


def _normalize_document(document: Any) -> dict[str, Any]:
    root = _exact_keys(document, {"schema", "work_items"}, "document")
    if root["schema"] != WORK_SCHEMA:
        raise PayoffPathError(f"document.schema must be {WORK_SCHEMA}")
    items = root["work_items"]
    if type(items) is not list:
        raise PayoffPathError("document.work_items must be an array")
    if len(items) > _MAX_ITEMS:
        raise PayoffPathError("document.work_items exceeds item limit")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(items):
        item = _exact_keys(
            raw,
            {
                "work_id",
                "opportunity_id",
                "started_at_utc",
                "free_work_budget_minutes",
                "free_work_spent_minutes",
                "payoff_path",
            },
            f"work_items[{index}]",
        )
        work_id = _opaque_ref(item["work_id"], f"work_items[{index}].work_id")
        if work_id in seen:
            raise PayoffPathError(f"duplicate work_id: {work_id}")
        seen.add(work_id)
        normalized.append(
            {
                "work_id": work_id,
                "opportunity_id": _opaque_ref(item["opportunity_id"], f"work_items[{index}].opportunity_id"),
                "started_at_utc": _render_timestamp(
                    _timestamp(item["started_at_utc"], f"work_items[{index}].started_at_utc")
                ),
                "free_work_budget_minutes": _int(
                    item["free_work_budget_minutes"],
                    f"work_items[{index}].free_work_budget_minutes",
                    minimum=1,
                    maximum=_MAX_FREE_MINUTES,
                ),
                "free_work_spent_minutes": _int(
                    item["free_work_spent_minutes"],
                    f"work_items[{index}].free_work_spent_minutes",
                    minimum=0,
                    maximum=_MAX_FREE_MINUTES,
                ),
                "payoff_path": _normalize_path(item["payoff_path"], f"work_items[{index}].payoff_path"),
            }
        )
    normalized.sort(key=lambda row: row["work_id"])
    return {"schema": WORK_SCHEMA, "work_items": normalized}


def _evaluate_item(item: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    budget = item["free_work_budget_minutes"]
    spent = item["free_work_spent_minutes"]
    remaining = max(budget - spent, 0)
    reasons: list[str] = []

    started_at = _timestamp(item["started_at_utc"], "normalized.started_at_utc")
    if started_at > as_of:
        reasons.append("WORK_STARTS_IN_FUTURE")

    path = item["payoff_path"]
    if spent >= budget:
        state = "STOP_UNPAID_WORK"
        reasons.append("FREE_WORK_BUDGET_EXHAUSTED")
    elif path is None:
        state = "HOLD_NO_PAYOFF_PATH"
        reasons.append("NO_EVIDENCE_BACKED_COMPENSATION_PATH")
    else:
        source = path["source"]
        conversion = path["conversion"]
        observed = _timestamp(source["observed_at_utc"], "normalized.source.observed_at_utc")
        due = _timestamp(conversion["due_at_utc"], "normalized.conversion.due_at_utc")
        if observed > as_of:
            reasons.append("SOURCE_EVIDENCE_FROM_FUTURE")
        else:
            max_age = timedelta(days=source["max_age_days"])
            if as_of - observed > max_age:
                reasons.append("SOURCE_EVIDENCE_STALE")
        if due <= as_of:
            reasons.append("CONVERSION_DEADLINE_EXPIRED")
        if due <= observed:
            reasons.append("CONVERSION_DEADLINE_NOT_AFTER_SOURCE_OBSERVATION")
        if reasons:
            state = "HOLD_STALE_OR_INVALID"
        else:
            state = "READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW"
            reasons.append("PAYOFF_PATH_CURRENT_AND_FREE_WORK_WITHIN_CAP")

    if state not in STATES:  # defensive invariant
        raise PayoffPathError("internal invalid state")

    path_summary: dict[str, Any] | None = None
    if path is not None:
        path_summary = {
            "mechanism": path["mechanism"],
            "value": path["value"],
            "canonical_source_url": path["source"]["canonical_url"],
            "source_evidence_ref": path["source"]["evidence_ref"],
            "source_evidence_sha256": path["source"]["evidence_sha256"],
            "source_observed_at_utc": path["source"]["observed_at_utc"],
            "conversion_event": path["conversion"]["event"],
            "conversion_due_at_utc": path["conversion"]["due_at_utc"],
            "conversion_evidence_ref": path["conversion"]["evidence_ref"],
            "conversion_evidence_sha256": path["conversion"]["evidence_sha256"],
        }

    return {
        "work_id": item["work_id"],
        "opportunity_id": item["opportunity_id"],
        "state": state,
        "reasons": reasons,
        "free_work_budget_minutes": budget,
        "free_work_spent_minutes": spent,
        "free_work_remaining_minutes": remaining,
        "payoff_path": path_summary,
    }


def _summary(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    result = {state: 0 for state in sorted(STATES)}
    total = 0
    for row in rows:
        total += 1
        result[row["state"]] += 1
    return {"total_items": total, **result}


def render_markdown(packet: dict[str, Any]) -> str:
    if type(packet) is not dict or packet.get("schema") != PACKET_SCHEMA:
        raise PayoffPathError(f"packet must be {PACKET_SCHEMA}")
    lines = [
        "# Payoff Path Gate Review",
        "",
        f"Evaluated at: `{packet['evaluated_at_utc']}`",
        f"Source document SHA-256: `{packet['source_document_sha256']}`",
        "",
        "> READY means owner review only. It does not authorize outreach, submission, spend, delivery, payment action, or a revenue claim.",
        "",
    ]
    if not packet["results"]:
        lines.append("No speculative work items were supplied.")
    for row in packet["results"]:
        lines.extend(
            [
                f"## {row['work_id']}",
                "",
                f"- Opportunity: `{row['opportunity_id']}`",
                f"- State: **{row['state']}**",
                f"- Unpaid budget: `{row['free_work_budget_minutes']}` min; spent: `{row['free_work_spent_minutes']}` min; remaining: `{row['free_work_remaining_minutes']}` min",
                f"- Reasons: {', '.join('`' + reason + '`' for reason in row['reasons'])}",
            ]
        )
        path = row["payoff_path"]
        if path is None:
            lines.append("- Payoff path: **none evidenced**")
        else:
            value = path["value"]
            if value["kind"] in {"FIXED", "POOL"}:
                value_text = f"{value['kind']} {value['currency']} {value['amount_minor']} minor units"
            else:
                value_text = value["kind"]
            lines.extend(
                [
                    f"- Mechanism: `{path['mechanism']}`; source value state: `{value_text}`",
                    f"- Source: `{path['canonical_source_url']}`",
                    f"- Source evidence: `{path['source_evidence_ref']}` / `{path['source_evidence_sha256']}`",
                    f"- Next conversion: `{path['conversion_event']}` by `{path['conversion_due_at_utc']}`",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def compile_gate(document: Any, trusted_as_of: datetime | str | None = None) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Compile deterministic owner-review evidence.

    Production callers should omit ``trusted_as_of`` so process UTC is authoritative.
    Tests and historical replay may inject an explicit trusted time through this library
    function; the command-line compile path intentionally exposes no as-of override.
    """

    as_of = _trusted_now(trusted_as_of)
    normalized = _normalize_document(document)
    rows = [_evaluate_item(item, as_of) for item in normalized["work_items"]]
    packet = {
        "schema": PACKET_SCHEMA,
        "evaluated_at_utc": _render_timestamp(as_of),
        "source_document_sha256": _digest(normalized),
        "summary": _summary(rows),
        "results": rows,
        "authority": "OWNER_REVIEW_ONLY_NO_EXTERNAL_ACTION",
    }
    markdown = render_markdown(packet)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "source_document_sha256": packet["source_document_sha256"],
        "packet_sha256": _digest(packet),
        "markdown_sha256": _sha256_bytes(markdown.encode("utf-8")),
    }
    return packet, markdown, receipt


def verify_gate(
    document: Any,
    packet: Any,
    markdown: Any,
    receipt: Any,
    trusted_now: datetime | str | None = None,
) -> bool:
    """Verify content-addressed evidence and re-check temporal validity at trusted now."""

    now = _trusted_now(trusted_now)
    normalized = _normalize_document(document)
    packet_obj = _exact_keys(
        packet,
        {"schema", "evaluated_at_utc", "source_document_sha256", "summary", "results", "authority"},
        "packet",
    )
    if packet_obj["schema"] != PACKET_SCHEMA or packet_obj["authority"] != "OWNER_REVIEW_ONLY_NO_EXTERNAL_ACTION":
        raise PayoffPathError("packet schema or authority is invalid")
    evaluated = _timestamp(packet_obj["evaluated_at_utc"], "packet.evaluated_at_utc")
    if evaluated > now:
        raise PayoffPathError("packet evaluation time is in the future")
    if packet_obj["source_document_sha256"] != _digest(normalized):
        raise PayoffPathError("packet source document digest mismatch")

    expected_packet, expected_markdown, expected_receipt = compile_gate(normalized, evaluated)
    if packet_obj != expected_packet:
        raise PayoffPathError("packet content does not match recompilation")
    if type(markdown) is not str or markdown != expected_markdown:
        raise PayoffPathError("Markdown content does not match recompilation")

    receipt_obj = _exact_keys(
        receipt,
        {"schema", "source_document_sha256", "packet_sha256", "markdown_sha256"},
        "receipt",
    )
    if receipt_obj["schema"] != RECEIPT_SCHEMA:
        raise PayoffPathError("receipt schema is invalid")
    for field in ("source_document_sha256", "packet_sha256", "markdown_sha256"):
        _sha(receipt_obj[field], f"receipt.{field}")
    if receipt_obj != expected_receipt:
        raise PayoffPathError("receipt content does not match exact packet/Markdown")

    # The original packet remains content-verifiable, but READY may not be treated as
    # current if evidence or a conversion deadline has expired since compilation.
    current_rows = [_evaluate_item(item, now) for item in normalized["work_items"]]
    previous = {row["work_id"]: row for row in expected_packet["results"]}
    for row in current_rows:
        before = previous[row["work_id"]]
        if before["state"] == "READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW" and row["state"] != before["state"]:
            raise PayoffPathError(
                f"previous READY item {row['work_id']} is no longer current at trusted verification time"
            )
    return True


def _read_regular(path: Path) -> str:
    try:
        info = path.lstat()
    except OSError as exc:
        raise PayoffPathError(f"cannot stat input: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PayoffPathError(f"input must be a regular non-symlink file: {path}")
    if info.st_size > _MAX_INPUT_BYTES:
        raise PayoffPathError(f"input file too large: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PayoffPathError(f"cannot read UTF-8 input: {path}") from exc


def _preflight_output(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PayoffPathError(f"cannot stat output: {path}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise PayoffPathError(f"refusing final symlink output: {path}")
    raise PayoffPathError(f"refusing to overwrite existing output: {path}")


def _exclusive_write(path: Path, text: str) -> None:
    _preflight_output(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(path), flags, 0o600)
    except OSError as exc:
        raise PayoffPathError(f"cannot create output exclusively: {path}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _publish_bundle(outputs: list[tuple[Path, str]]) -> None:
    destinations = [path.resolve(strict=False) for path, _ in outputs]
    if len(set(destinations)) != len(destinations):
        raise PayoffPathError("output paths must be distinct")
    for path, _ in outputs:
        _preflight_output(path)
        if not path.parent.exists() or not path.parent.is_dir():
            raise PayoffPathError(f"output parent must be an existing directory: {path.parent}")
    created: list[Path] = []
    try:
        for path, text in outputs:
            _exclusive_write(path, text)
            created.append(path)
    except Exception:
        for path in created:
            try:
                path.unlink()
            except OSError:
                pass
        raise


def _compile_command(args: argparse.Namespace) -> int:
    document = load_strict_json(_read_regular(Path(args.input)))
    packet, markdown, receipt = compile_gate(document)
    _publish_bundle(
        [
            (Path(args.packet), _canonical_json(packet) + "\n"),
            (Path(args.markdown), markdown),
            (Path(args.receipt), _canonical_json(receipt) + "\n"),
        ]
    )
    print(_canonical_json(receipt))
    return 0


def _verify_command(args: argparse.Namespace) -> int:
    document = load_strict_json(_read_regular(Path(args.input)))
    packet = load_strict_json(_read_regular(Path(args.packet)))
    markdown = _read_regular(Path(args.markdown))
    receipt = load_strict_json(_read_regular(Path(args.receipt)))
    verify_gate(document, packet, markdown, receipt)
    print(_canonical_json({"verified": True, "packet_sha256": receipt["packet_sha256"]}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    compile_parser = sub.add_parser("compile", help="compile an owner-review payoff-path packet")
    compile_parser.add_argument("--input", required=True)
    compile_parser.add_argument("--packet", required=True)
    compile_parser.add_argument("--markdown", required=True)
    compile_parser.add_argument("--receipt", required=True)
    compile_parser.set_defaults(func=_compile_command)

    verify_parser = sub.add_parser("verify", help="verify exact inputs, packet, Markdown, receipt, and current temporal validity")
    verify_parser.add_argument("--input", required=True)
    verify_parser.add_argument("--packet", required=True)
    verify_parser.add_argument("--markdown", required=True)
    verify_parser.add_argument("--receipt", required=True)
    verify_parser.set_defaults(func=_verify_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return int(args.func(args))
    except PayoffPathError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
