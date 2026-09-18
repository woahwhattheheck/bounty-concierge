# SPDX-License-Identifier: MIT
"""Evidence-bound bridge from one authorized provider attempt into durable history.

``payout_delivery_gate`` answers whether one exact outbound mutation may be
attempted. This module records what the operator/provider observed *after* that
attempt and emits a mutation row that can be inserted into the next payout
history generation.

The bridge deliberately does not send anything and does not update collection
custody. A connector exception by itself is ambiguous: it is never promoted to
"not sent". Only an explicit post-attempt provider census (or evidence that the
attempt was blocked before reaching the provider) may establish
``CONFIRMED_NOT_SENT``. Only an explicit provider send receipt may establish
``CONFIRMED_SENT``.
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
import sys
from typing import Any, Dict, Optional, Sequence, Tuple

DELIVERY_SCHEMA = "bounty-concierge-payout-delivery-receipt/v1"
REPORT_SCHEMA = "bounty-provider-attempt-report/v1"
RECEIPT_SCHEMA = "bounty-provider-attempt-continuity/v1"

MAX_JSON_BYTES = 1024 * 1024
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_OUTCOMES = frozenset({"CONFIRMED_SENT", "CONFIRMED_NOT_SENT", "AMBIGUOUS"})
_EVIDENCE_CLASSES = frozenset(
    {
        "PROVIDER_SENT_RECEIPT",
        "POST_ATTEMPT_PROVIDER_CENSUS",
        "PRE_PROVIDER_BLOCK",
        "CONNECTOR_ERROR",
    }
)
_REPORT_KEYS = frozenset(
    {
        "schema",
        "attempt_id",
        "attempted_at",
        "outcome",
        "evidence_class",
        "evidence_sha256",
        "provider_receipt_key",
    }
)
_DELIVERY_REQUIRED = frozenset(
    {
        "schema",
        "as_of",
        "receipt_sha256",
        "request_sha256",
        "history_sha256",
        "request_id",
        "opportunity_ref",
        "target_route_sha256",
        "message_sha256",
        "owner_seat",
        "lease_issued_at",
        "lease_expires_at",
        "state",
        "one_provider_mutation_authorized",
        "external_action_performed",
        "payment_inferred",
        "cash_inferred",
        "revenue_inferred",
    }
)


class ProviderAttemptError(ValueError):
    """Malformed, contradictory, or authority-expanding attempt evidence."""


def _canonical_json(value: Any) -> bytes:
    try:
        raw = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProviderAttemptError("value is not canonical JSON") from exc
    if len(raw) > MAX_JSON_BYTES:
        raise ProviderAttemptError("canonical JSON exceeds 1 MiB")
    return raw


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _hex64(value: Any, field: str) -> str:
    if type(value) is not str or not _HEX64.fullmatch(value):
        raise ProviderAttemptError("%s must be lowercase SHA-256" % field)
    return value


def _safe_id(value: Any, field: str) -> str:
    if type(value) is not str or not _SAFE_ID.fullmatch(value):
        raise ProviderAttemptError("%s must be a safe opaque identifier" % field)
    return value


def _role(value: Any, field: str) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > 128:
        raise ProviderAttemptError("%s must be a bounded role label" % field)
    lowered = value.casefold()
    if any(
        marker in lowered
        for marker in ("http://", "https://", "@", "password", "secret", "token=")
    ):
        raise ProviderAttemptError(
            "%s must not contain contact or secret material" % field
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ProviderAttemptError("%s contains unsupported text" % field)
    return value


def _timestamp(value: Any, field: str) -> Tuple[str, datetime]:
    if type(value) is not str or not _UTC.fullmatch(value):
        raise ProviderAttemptError("%s must be canonical UTC seconds" % field)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProviderAttemptError("%s must be a valid UTC timestamp" % field) from exc
    return value, parsed


def _strict_report(raw: Any) -> Dict[str, Any]:
    if type(raw) is not dict or set(raw) != _REPORT_KEYS:
        raise ProviderAttemptError("attempt report has an invalid field set")
    if raw["schema"] != REPORT_SCHEMA:
        raise ProviderAttemptError("attempt report schema is unsupported")
    outcome = raw["outcome"]
    if outcome not in _OUTCOMES:
        raise ProviderAttemptError("attempt outcome is unsupported")
    evidence_class = raw["evidence_class"]
    if evidence_class not in _EVIDENCE_CLASSES:
        raise ProviderAttemptError("attempt evidence_class is unsupported")

    allowed = {
        "CONFIRMED_SENT": frozenset({"PROVIDER_SENT_RECEIPT"}),
        "CONFIRMED_NOT_SENT": frozenset(
            {"POST_ATTEMPT_PROVIDER_CENSUS", "PRE_PROVIDER_BLOCK"}
        ),
        "AMBIGUOUS": frozenset({"CONNECTOR_ERROR"}),
    }
    if evidence_class not in allowed[outcome]:
        raise ProviderAttemptError(
            "attempt outcome is not supported by its evidence_class"
        )

    provider_key = raw["provider_receipt_key"]
    if outcome == "CONFIRMED_SENT":
        provider_key = _safe_id(
            provider_key, "attempt.provider_receipt_key"
        )
    elif provider_key is not None:
        raise ProviderAttemptError(
            "non-sent attempts must not carry a provider receipt key"
        )

    attempted_text, _ = _timestamp(
        raw["attempted_at"], "attempt.attempted_at"
    )
    return {
        "schema": REPORT_SCHEMA,
        "attempt_id": _safe_id(raw["attempt_id"], "attempt.attempt_id"),
        "attempted_at": attempted_text,
        "outcome": outcome,
        "evidence_class": evidence_class,
        "evidence_sha256": _hex64(
            raw["evidence_sha256"], "attempt.evidence_sha256"
        ),
        "provider_receipt_key": provider_key,
    }


def _normalize_delivery(raw: Any) -> Dict[str, Any]:
    if type(raw) is not dict:
        raise ProviderAttemptError("delivery receipt must be an object")
    missing = sorted(_DELIVERY_REQUIRED - set(raw))
    if missing:
        raise ProviderAttemptError(
            "delivery receipt is missing required fields: %s"
            % ",".join(missing)
        )
    if raw["schema"] != DELIVERY_SCHEMA:
        raise ProviderAttemptError("delivery receipt schema is unsupported")

    supplied_digest = _hex64(
        raw["receipt_sha256"], "delivery.receipt_sha256"
    )
    body = dict(raw)
    body.pop("receipt_sha256", None)
    if _digest(body) != supplied_digest:
        raise ProviderAttemptError(
            "delivery receipt digest does not match its bytes"
        )

    if (
        raw["state"] != "READY_FOR_ONE_PROVIDER_MUTATION"
        or raw["one_provider_mutation_authorized"] is not True
    ):
        raise ProviderAttemptError(
            "delivery receipt does not authorize one provider mutation"
        )
    for field in (
        "external_action_performed",
        "payment_inferred",
        "cash_inferred",
        "revenue_inferred",
    ):
        if raw[field] is not False:
            raise ProviderAttemptError(
                "delivery receipt expands authority at %s" % field
            )

    as_of_text, as_of = _timestamp(raw["as_of"], "delivery.as_of")
    issued_text, issued = _timestamp(
        raw["lease_issued_at"], "delivery.lease_issued_at"
    )
    expires_text, expires = _timestamp(
        raw["lease_expires_at"], "delivery.lease_expires_at"
    )
    if expires <= issued or not (issued <= as_of < expires):
        raise ProviderAttemptError(
            "delivery receipt lease timing is inconsistent"
        )

    return {
        "receipt_sha256": supplied_digest,
        "as_of": as_of_text,
        "request_sha256": _hex64(
            raw["request_sha256"], "delivery.request_sha256"
        ),
        "history_sha256": _hex64(
            raw["history_sha256"], "delivery.history_sha256"
        ),
        "request_id": _safe_id(raw["request_id"], "delivery.request_id"),
        "opportunity_ref": _safe_id(
            raw["opportunity_ref"], "delivery.opportunity_ref"
        ),
        "target_route_sha256": _hex64(
            raw["target_route_sha256"], "delivery.target_route_sha256"
        ),
        "message_sha256": _hex64(
            raw["message_sha256"], "delivery.message_sha256"
        ),
        "owner_seat": _role(raw["owner_seat"], "delivery.owner_seat"),
        "lease_issued_at": issued_text,
        "lease_expires_at": expires_text,
    }


def compile_provider_attempt(
    delivery_receipt: Any,
    attempt_report: Any,
    *,
    as_of: datetime,
) -> Dict[str, Any]:
    """Bind one observed attempt to one exact payout-delivery authorization receipt."""
    if (
        not isinstance(as_of, datetime)
        or as_of.tzinfo is None
        or as_of.utcoffset() is None
    ):
        raise ProviderAttemptError("as_of must be timezone-aware")
    now = as_of.astimezone(timezone.utc)
    delivery = _normalize_delivery(delivery_receipt)
    report = _strict_report(attempt_report)

    _, gate_time = _timestamp(delivery["as_of"], "delivery.as_of")
    _, lease_issued = _timestamp(
        delivery["lease_issued_at"], "delivery.lease_issued_at"
    )
    _, lease_expires = _timestamp(
        delivery["lease_expires_at"], "delivery.lease_expires_at"
    )
    _, attempted = _timestamp(
        report["attempted_at"], "attempt.attempted_at"
    )
    if attempted < gate_time or attempted < lease_issued:
        raise ProviderAttemptError(
            "attempt predates the exact delivery authorization"
        )
    if attempted >= lease_expires:
        raise ProviderAttemptError(
            "attempt falls outside the selected-owner lease"
        )
    if attempted > now:
        raise ProviderAttemptError("attempt is in the future")

    outcome = report["outcome"]
    if outcome == "CONFIRMED_SENT":
        continuity_state = "RECORD_DISPATCH"
        custody_dispatch_allowed = True
        retry_requires_fresh_gate = False
    elif outcome == "CONFIRMED_NOT_SENT":
        continuity_state = "RECAPTURE_AND_REAUTHORIZE_BEFORE_RETRY"
        custody_dispatch_allowed = False
        retry_requires_fresh_gate = True
    else:
        continuity_state = "HOLD_FOR_PROVIDER_CENSUS"
        custody_dispatch_allowed = False
        retry_requires_fresh_gate = True

    history_mutation = {
        "mutation_id": report["attempt_id"],
        "request_sha256": delivery["request_sha256"],
        "target_route_sha256": delivery["target_route_sha256"],
        "opportunity_ref": delivery["opportunity_ref"],
        "message_sha256": delivery["message_sha256"],
        "owner_seat": delivery["owner_seat"],
        "attempted_at": report["attempted_at"],
        "outcome": outcome,
        "provider_receipt_sha256": report["evidence_sha256"],
    }

    core = {
        "schema": RECEIPT_SCHEMA,
        "as_of": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "delivery_receipt_sha256": delivery["receipt_sha256"],
        "delivery_history_sha256": delivery["history_sha256"],
        "attempt_report_sha256": _digest(report),
        "attempt_id": report["attempt_id"],
        "request_id": delivery["request_id"],
        "request_sha256": delivery["request_sha256"],
        "opportunity_ref": delivery["opportunity_ref"],
        "target_route_sha256": delivery["target_route_sha256"],
        "message_sha256": delivery["message_sha256"],
        "owner_seat": delivery["owner_seat"],
        "attempted_at": report["attempted_at"],
        "outcome": outcome,
        "evidence_class": report["evidence_class"],
        "evidence_sha256": report["evidence_sha256"],
        "provider_receipt_key": report["provider_receipt_key"],
        "continuity_state": continuity_state,
        "history_mutation": history_mutation,
        "custody_dispatch_allowed": custody_dispatch_allowed,
        "retry_requires_fresh_gate": retry_requires_fresh_gate,
        "authority": {
            "external_action_performed_by_module": False,
            "provider_receipt_authenticated_by_module": False,
            "connector_error_proves_not_sent": False,
            "advertised_reward_is_debt": False,
            "payment_inferred": False,
            "cash_inferred": False,
            "revenue_inferred": False,
        },
    }
    receipt = dict(core)
    receipt["receipt_sha256"] = _digest(core)
    return receipt


def verify_provider_attempt(
    delivery_receipt: Any,
    attempt_report: Any,
    receipt: Any,
) -> Dict[str, Any]:
    if type(receipt) is not dict:
        raise ProviderAttemptError("continuity receipt must be an object")
    _, as_of = _timestamp(receipt.get("as_of"), "receipt.as_of")
    expected = compile_provider_attempt(
        delivery_receipt, attempt_report, as_of=as_of
    )
    if _canonical_json(expected) != _canonical_json(receipt):
        raise ProviderAttemptError(
            "continuity receipt differs from deterministic recompilation"
        )
    return json.loads(_canonical_json(expected).decode("utf-8"))


def _reject_duplicate_keys(
    pairs: Sequence[Tuple[str, Any]]
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ProviderAttemptError("duplicate JSON key: %s" % key)
        out[key] = value
    return out


def _read_json(path: Path) -> Any:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise ProviderAttemptError("cannot open regular input") from exc
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size < 0
            or info.st_size > MAX_JSON_BYTES
        ):
            raise ProviderAttemptError(
                "input must be a bounded regular file"
            )
        chunks = []
        remaining = info.st_size + 1
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_JSON_BYTES:
            raise ProviderAttemptError("input exceeds 1 MiB")
    finally:
        os.close(fd)
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ProviderAttemptError(
                    "non-finite JSON constant: %s" % value
                )
            ),
        )
    except ProviderAttemptError:
        raise
    except (
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
    ) as exc:
        raise ProviderAttemptError(
            "input is not valid strict JSON"
        ) from exc


def _write_new(path: Path, value: Dict[str, Any]) -> None:
    payload = (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
    )
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fd = os.open(str(path), flags, 0o600)
    except OSError as exc:
        raise ProviderAttemptError(
            "refusing to overwrite or follow output path"
        ) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ProviderAttemptError(
                "output must be a regular file"
            )
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise ProviderAttemptError("short output write")
            view = view[written:]
        os.fsync(fd)
    except Exception:
        try:
            os.close(fd)
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    else:
        os.close(fd)


def _emit(
    value: Dict[str, Any],
    output: Optional[Path],
) -> None:
    if output is None:
        sys.stdout.write(
            json.dumps(
                value,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n"
        )
    else:
        _write_new(output, value)


def main(
    argv: Optional[Sequence[str]] = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.provider_attempt_continuity",
        description=(
            "Bind one observed connector/provider attempt to one exact "
            "payout-delivery authorization. This command performs no "
            "external action."
        ),
    )
    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )
    compile_p = sub.add_parser("compile")
    compile_p.add_argument(
        "delivery_receipt",
        type=Path,
    )
    compile_p.add_argument(
        "attempt_report",
        type=Path,
    )
    compile_p.add_argument(
        "--output",
        type=Path,
    )
    verify_p = sub.add_parser("verify")
    verify_p.add_argument(
        "delivery_receipt",
        type=Path,
    )
    verify_p.add_argument(
        "attempt_report",
        type=Path,
    )
    verify_p.add_argument(
        "receipt",
        type=Path,
    )
    verify_p.add_argument(
        "--output",
        type=Path,
    )
    args = parser.parse_args(
        list(argv) if argv is not None else None
    )
    try:
        delivery = _read_json(args.delivery_receipt)
        report = _read_json(args.attempt_report)
        if args.command == "compile":
            result = compile_provider_attempt(
                delivery,
                report,
                as_of=datetime.now(timezone.utc),
            )
        else:
            result = verify_provider_attempt(
                delivery,
                report,
                _read_json(args.receipt),
            )
        _emit(result, args.output)
    except (ProviderAttemptError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
