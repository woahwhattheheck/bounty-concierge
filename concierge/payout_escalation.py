# SPDX-License-Identifier: MIT
"""Evidence-bound payout timing/escalation readiness for merged RTC bounty work.

This module fills the operational gap between ``revenue_closeout`` and
``revenue_settlement``.  It never recognizes cash.  Instead it evaluates the
repository's versioned RustChain payout timing expectations against a merged
closeout snapshot and explicitly operator-bound wallet-history evidence.

Production CLI time is verifier-owned UTC.  The pure compiler accepts an
explicit ``as_of`` only so callers/tests can reproduce deterministic receipts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit
from typing import Any, Mapping


_SCHEMA_VERSION = 1
_POLICY = {
    "policy_version": "rustchain-bounty-payout-timing/2026-09-07/v1",
    "source_ref": "docs/PAYOUT_GUIDE.md#Payout-Timeline",
    "source_git_blob_sha1": "463ba80ccff616683aa9a85d691aadba74d25966",
    "currency": "RTC",
    "transfer_initiation_hours": 24,
    "pending_confirmation_hours": 24,
}
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TX_ID_RE = re.compile(r"^[!-~]{1,256}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_MAX_EPOCH_SECONDS = 253402300799
_MAX_ITEMS = 10_000
_MAX_ROWS_PER_ITEM = 100
_MAX_JSON_BYTES = 4 * 1024 * 1024
_PENDING = frozenset({"pending", "confirming"})
_TERMINAL = frozenset({"confirmed"})
_FAILED = frozenset({"failed"})


class PayoutEscalationInputError(ValueError):
    """Malformed, ambiguous, or policy-incompatible operator input."""


class PayoutEscalationEvidenceError(RuntimeError):
    """Bound evidence does not support a unique payout-timing decision."""


def _canonical_json(value: Any) -> bytes:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PayoutEscalationInputError("value is not canonical JSON") from exc
    if len(payload) > _MAX_JSON_BYTES:
        raise PayoutEscalationInputError("canonical JSON value is too large")
    return payload


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def policy_receipt() -> dict[str, Any]:
    receipt = dict(_POLICY)
    receipt["policy_sha256"] = _sha256(_POLICY)
    return receipt


def closeout_snapshot_sha256(payload: Any) -> str:
    """Self-integrity digest for one exact closeout snapshot (not external auth)."""
    return _sha256(payload)


def history_capture_sha256(payload: Any) -> str:
    """Self-integrity digest for one exact history capture (not provider auth)."""
    return _sha256(payload)


def _exact_int(value: Any, *, field: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PayoutEscalationInputError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise PayoutEscalationInputError(f"{field} must be >= {minimum}")
    return value


def _parse_timestamp(value: Any, *, field: str) -> datetime:
    if type(value) is not str or not _UTC_RE.fullmatch(value):
        raise PayoutEscalationInputError(f"{field} must be canonical UTC with at most microsecond precision")
    text = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PayoutEscalationInputError(f"{field} must be a canonical ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PayoutEscalationInputError(f"{field} must include a timezone")
    utc = parsed.astimezone(timezone.utc)
    canonical = utc.isoformat(timespec="seconds").replace("+00:00", "Z")
    normalized_input = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if "." not in value and canonical != normalized_input:
        raise PayoutEscalationInputError(f"{field} must be canonical UTC")
    return utc


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="auto").replace("+00:00", "Z")


def _parse_history_timestamp(value: Any, *, field: str) -> datetime:
    if isinstance(value, bool):
        raise PayoutEscalationInputError(f"{field} must be canonical UTC text or Unix seconds")
    if isinstance(value, int):
        if value < 0 or value > _MAX_EPOCH_SECONDS:
            raise PayoutEscalationInputError(f"{field} Unix seconds are out of range")
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise PayoutEscalationInputError(f"{field} Unix seconds are out of range") from exc
    return _parse_timestamp(value, field=field)


def _positive_decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise PayoutEscalationInputError(f"{field} must be a positive decimal")
    source = str(value)
    if len(source) > 64:
        raise PayoutEscalationInputError(f"{field} representation is too large")
    try:
        amount = Decimal(source)
    except (InvalidOperation, ValueError) as exc:
        raise PayoutEscalationInputError(f"{field} must be a positive decimal") from exc
    exponent = amount.as_tuple().exponent
    if (
        not amount.is_finite()
        or amount <= 0
        or len(amount.as_tuple().digits) > 30
        or not isinstance(exponent, int)
        or abs(exponent) > 18
    ):
        raise PayoutEscalationInputError(f"{field} must be a bounded positive decimal")
    return amount


def _amount_text(amount: Decimal) -> str:
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _identity(raw: Mapping[str, Any], *, field: str) -> tuple[str, int]:
    if not isinstance(raw, Mapping):
        raise PayoutEscalationInputError(f"{field} must be an object")
    repo = raw.get("repo")
    pr = raw.get("pr")
    if type(repo) is not str or not _REPO_RE.fullmatch(repo):
        raise PayoutEscalationInputError(f"{field}.repo must be owner/name")
    owner, name = repo.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise PayoutEscalationInputError(f"{field}.repo contains a dot path segment")
    _exact_int(pr, field=f"{field}.pr", minimum=1)
    return repo, pr


def _closeout_index(payload: Any, *, as_of: datetime) -> tuple[list[dict[str, Any]], dict[tuple[str, int], dict[str, Any]]]:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "items"}:
        raise PayoutEscalationInputError("closeout must contain only schema_version and items")
    if payload.get("schema_version") != 1:
        raise PayoutEscalationInputError("closeout schema_version must be 1")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) > _MAX_ITEMS:
        raise PayoutEscalationInputError("closeout items must be a bounded list")
    ordered: list[dict[str, Any]] = []
    indexed: dict[tuple[str, int], dict[str, Any]] = {}
    for idx, raw in enumerate(items):
        repo, pr = _identity(raw, field=f"closeout.items[{idx}]")
        key = (repo.casefold(), pr)
        if key in indexed:
            raise PayoutEscalationInputError(f"duplicate closeout identity: {repo}#{pr}")
        if raw.get("state") != "MERGED":
            raise PayoutEscalationInputError(f"{repo}#{pr} must be MERGED")
        if raw.get("cash_status") != "not_inferred":
            raise PayoutEscalationInputError(f"{repo}#{pr} cash_status must remain not_inferred")
        if raw.get("currency") != _POLICY["currency"]:
            raise PayoutEscalationInputError(f"{repo}#{pr} is outside {_POLICY['currency']} payout policy")
        canonical_url = raw.get("canonical_url")
        if canonical_url != f"https://github.com/{repo}/pull/{pr}":
            raise PayoutEscalationInputError(f"{repo}#{pr} canonical_url is inconsistent")
        head_sha = raw.get("head_sha")
        if type(head_sha) is not str or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
            raise PayoutEscalationInputError(f"{repo}#{pr} head_sha must be lowercase 40-hex")
        if raw.get("next_action") not in {"route_settlement_followup", "monitor_settlement"}:
            raise PayoutEscalationInputError(
                f"{repo}#{pr} still has a higher-priority closeout obligation"
            )
        settlement_url = raw.get("settlement_followup_url")
        if settlement_url is not None:
            if type(settlement_url) is not str:
                raise PayoutEscalationInputError(f"{repo}#{pr} settlement_followup_url is invalid")
            parsed_url = urlsplit(settlement_url)
            if parsed_url.scheme not in {"https", "http"} or not parsed_url.netloc:
                raise PayoutEscalationInputError(f"{repo}#{pr} settlement_followup_url is invalid")
        merged_at = _parse_timestamp(raw.get("merged_at"), field=f"{repo}#{pr}.merged_at")
        if merged_at > as_of:
            raise PayoutEscalationEvidenceError(f"{repo}#{pr} merge timestamp is in the future")
        amount = _positive_decimal(raw.get("advertised_amount"), field=f"{repo}#{pr}.advertised_amount")
        item = {
            "repo": repo,
            "pr": pr,
            "merged_at": merged_at,
            "advertised_amount": amount,
            "settlement_followup_url": settlement_url,
        }
        ordered.append(item)
        indexed[key] = item
    return ordered, indexed


def history_row_sha256(row: Any, *, wallet: str) -> str:
    if not isinstance(row, dict):
        raise PayoutEscalationInputError("wallet history row must be an object")
    if type(wallet) is not str or not wallet or wallet != wallet.strip():
        raise PayoutEscalationInputError("wallet must be one non-empty identifier")
    return hashlib.sha256(_canonical_json({"wallet": wallet, "row": row})).hexdigest()


def _history_capture(payload: Any) -> tuple[str, str, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise PayoutEscalationInputError("history capture must be an object")
    if set(payload) == {"ok", "miner_id", "transactions", "total"}:
        if payload.get("ok") is not True:
            raise PayoutEscalationEvidenceError("canonical wallet history did not report ok=true")
        wallet = payload.get("miner_id")
        rows = payload.get("transactions")
        total = payload.get("total")
        if type(wallet) is not str or not wallet or wallet != wallet.strip():
            raise PayoutEscalationInputError("canonical wallet history miner_id is invalid")
        if not isinstance(rows, list) or len(rows) > _MAX_ITEMS:
            raise PayoutEscalationInputError("canonical wallet history transactions must be bounded")
        _exact_int(total, field="canonical wallet history total", minimum=0)
        if total != len(rows):
            raise PayoutEscalationEvidenceError("canonical wallet history total does not match snapshot")
        source = "queried_wallet"
    elif set(payload) == {"schema_version", "source", "wallet", "items"}:
        if payload.get("schema_version") != 1 or payload.get("source") != "rustchain_wallet_history":
            raise PayoutEscalationInputError("normalized history capture authority fields are invalid")
        wallet = payload.get("wallet")
        rows = payload.get("items")
        if type(wallet) is not str or not wallet or wallet != wallet.strip():
            raise PayoutEscalationInputError("normalized history wallet is invalid")
        if not isinstance(rows, list) or len(rows) > _MAX_ITEMS:
            raise PayoutEscalationInputError("normalized history items must be bounded")
        source = "captured_wallet"
    else:
        raise PayoutEscalationInputError("history capture must preserve exact wallet provenance")
    for row in rows:
        if not isinstance(row, dict):
            raise PayoutEscalationInputError("wallet history row must be an object")
    return wallet, source, rows


def _binding_index(
    payload: Any,
    *,
    wallet: str,
    closeout_payload: Any,
    history_payload: Any,
) -> dict[tuple[str, int], list[str]]:
    expected_keys = {
        "schema_version",
        "policy_version",
        "wallet",
        "closeout_sha256",
        "history_capture_sha256",
        "items",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise PayoutEscalationInputError(
            "bindings must contain the exact version/wallet/snapshot commitments and items"
        )
    if payload.get("schema_version") != 1:
        raise PayoutEscalationInputError("bindings schema_version must be 1")
    if payload.get("policy_version") != _POLICY["policy_version"]:
        raise PayoutEscalationInputError("bindings policy_version is stale or unknown")
    if payload.get("wallet") != wallet:
        raise PayoutEscalationEvidenceError("binding wallet does not match history provenance")
    closeout_digest = payload.get("closeout_sha256")
    history_digest = payload.get("history_capture_sha256")
    if type(closeout_digest) is not str or not _SHA256_RE.fullmatch(closeout_digest):
        raise PayoutEscalationInputError("bindings closeout_sha256 must be lowercase SHA-256")
    if type(history_digest) is not str or not _SHA256_RE.fullmatch(history_digest):
        raise PayoutEscalationInputError("bindings history_capture_sha256 must be lowercase SHA-256")
    if closeout_digest != closeout_snapshot_sha256(closeout_payload):
        raise PayoutEscalationEvidenceError("bindings are stale for this closeout snapshot")
    if history_digest != history_capture_sha256(history_payload):
        raise PayoutEscalationEvidenceError("bindings are stale for this history capture")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) > _MAX_ITEMS:
        raise PayoutEscalationInputError("binding items must be a bounded list")
    result: dict[tuple[str, int], list[str]] = {}
    used_rows: set[str] = set()
    for idx, raw in enumerate(items):
        if not isinstance(raw, dict) or set(raw) != {"repo", "pr", "history_sha256s"}:
            raise PayoutEscalationInputError(f"bindings.items[{idx}] has unknown or missing fields")
        repo, pr = _identity(raw, field=f"bindings.items[{idx}]")
        key = (repo.casefold(), pr)
        if key in result:
            raise PayoutEscalationInputError(f"duplicate binding identity: {repo}#{pr}")
        hashes = raw.get("history_sha256s")
        if not isinstance(hashes, list) or len(hashes) > _MAX_ROWS_PER_ITEM:
            raise PayoutEscalationInputError("history_sha256s must be a bounded list")
        selected: list[str] = []
        for value in hashes:
            if type(value) is not str or not _SHA256_RE.fullmatch(value):
                raise PayoutEscalationInputError("history_sha256s must be lowercase SHA-256 values")
            if value in selected:
                raise PayoutEscalationInputError("binding repeats one history row")
            if value in used_rows:
                raise PayoutEscalationEvidenceError("one history row cannot be bound to multiple PRs")
            selected.append(value)
            used_rows.add(value)
        result[key] = selected
    return result


def _one_identity(row: dict[str, Any], keys: tuple[str, ...], *, field: str, required: bool) -> str | None:
    values: list[str] = []
    for key in keys:
        if key not in row:
            continue
        value = row[key]
        if type(value) is not str or not value or value != value.strip() or any(ch.isspace() or not ch.isprintable() for ch in value):
            raise PayoutEscalationEvidenceError(f"wallet history row has malformed {field}")
        values.append(value)
    if not values:
        if required:
            raise PayoutEscalationEvidenceError(f"wallet history row omitted {field}")
        return None
    if len(set(values)) != 1:
        raise PayoutEscalationEvidenceError(f"wallet history row has conflicting {field} identities")
    return values[0]


def _row_timestamp(row: dict[str, Any], *, required: bool) -> datetime | None:
    values = [row[key] for key in ("timestamp", "created_at") if key in row]
    if not values:
        if required:
            raise PayoutEscalationEvidenceError("nonterminal wallet history row omitted initiation timestamp")
        return None
    parsed = [_parse_history_timestamp(value, field="wallet history timestamp") for value in values]
    if len(set(parsed)) != 1:
        raise PayoutEscalationEvidenceError("wallet history row has conflicting initiation timestamps")
    return parsed[0]


def _history_amount(row: dict[str, Any]) -> Decimal:
    present = [key for key in ("amount", "amount_rtc") if key in row]
    if not present:
        raise PayoutEscalationEvidenceError("wallet history row omitted amount")
    amounts = [_positive_decimal(row[key], field=f"wallet history {key}") for key in present]
    if len(set(amounts)) != 1:
        raise PayoutEscalationEvidenceError("wallet history row has conflicting amount fields")
    return amounts[0]


def _parse_bound_row(row: dict[str, Any], *, wallet: str, merged_at: datetime, as_of: datetime) -> dict[str, Any]:
    if row.get("type") != "transfer_in":
        raise PayoutEscalationEvidenceError("bound row is not a canonical incoming transfer")
    recipient = _one_identity(row, ("to", "to_addr", "recipient", "wallet"), field="recipient", required=False)
    if recipient is not None and recipient != wallet:
        raise PayoutEscalationEvidenceError("bound row conflicts with wallet provenance")
    sender = _one_identity(row, ("from", "from_addr", "sender"), field="sender", required=True)
    if sender == wallet:
        raise PayoutEscalationEvidenceError("bound row is self-funded")
    txid = row.get("tx_hash")
    if type(txid) is not str or not _TX_ID_RE.fullmatch(txid):
        raise PayoutEscalationEvidenceError("canonical incoming row requires one printable tx_hash")
    status_raw = row.get("status", "confirmed")
    if type(status_raw) is not str or status_raw != status_raw.strip():
        raise PayoutEscalationEvidenceError("wallet history row has malformed status")
    if status_raw in _PENDING:
        status = "pending"
    elif status_raw in _TERMINAL:
        status = "confirmed"
    elif status_raw in _FAILED:
        status = "failed"
    else:
        raise PayoutEscalationEvidenceError("wallet history row has unknown payout status")
    initiated_at = _row_timestamp(row, required=True)
    if initiated_at > as_of:
        raise PayoutEscalationEvidenceError("wallet history row initiation timestamp is in the future")
    if initiated_at < merged_at:
        raise PayoutEscalationEvidenceError("bound transfer predates the merged work")
    return {
        "status": status,
        "tx_hash": txid,
        "amount": _history_amount(row),
        "initiated_at": initiated_at,
    }


def compile_payout_escalation(
    closeout_payload: Any,
    history_payload: Any,
    bindings_payload: Any,
    *,
    as_of: datetime,
) -> dict[str, Any]:
    """Compile a deterministic owner-review timing receipt without cash inference."""
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise PayoutEscalationInputError("as_of must be a timezone-aware datetime")
    as_of = as_of.astimezone(timezone.utc)
    ordered, closeouts = _closeout_index(closeout_payload, as_of=as_of)
    wallet, history_source, rows = _history_capture(history_payload)
    bindings = _binding_index(
        bindings_payload,
        wallet=wallet,
        closeout_payload=closeout_payload,
        history_payload=history_payload,
    )
    for key in bindings:
        if key not in closeouts:
            raise PayoutEscalationInputError("binding references a PR absent from closeout")
    for key in closeouts:
        if key not in bindings:
            raise PayoutEscalationInputError("every closeout item requires an explicit binding")

    history: dict[str, dict[str, Any]] = {}
    for row in rows:
        fingerprint = history_row_sha256(row, wallet=wallet)
        if fingerprint in history:
            raise PayoutEscalationEvidenceError("wallet history contains duplicate indistinguishable rows")
        history[fingerprint] = row

    seen_txids: set[str] = set()
    output_items: list[dict[str, Any]] = []
    transfer_delta = timedelta(hours=_POLICY["transfer_initiation_hours"])
    pending_delta = timedelta(hours=_POLICY["pending_confirmation_hours"])

    for item in ordered:
        repo, pr = item["repo"], item["pr"]
        key = (repo.casefold(), pr)
        selected_hashes = bindings.get(key, [])
        parsed_rows: list[dict[str, Any]] = []
        selected_total = Decimal(0)
        with localcontext() as ctx:
            ctx.prec = 128
            for fingerprint in selected_hashes:
                raw = history.get(fingerprint)
                if raw is None:
                    raise PayoutEscalationEvidenceError(f"{repo}#{pr} binding references absent history evidence")
                parsed = _parse_bound_row(raw, wallet=wallet, merged_at=item["merged_at"], as_of=as_of)
                if parsed["tx_hash"] in seen_txids:
                    raise PayoutEscalationEvidenceError("one wallet transaction identity cannot support multiple payout decisions")
                seen_txids.add(parsed["tx_hash"])
                selected_total += parsed["amount"]
                parsed_rows.append({**parsed, "history_sha256": fingerprint})
        if selected_total > item["advertised_amount"]:
            raise PayoutEscalationEvidenceError(f"{repo}#{pr} bound transfer evidence exceeds advertised amount")

        transfer_due = item["merged_at"] + transfer_delta
        pending_rows = [row for row in parsed_rows if row["status"] == "pending"]
        failed_rows = [row for row in parsed_rows if row["status"] == "failed"]
        confirmed_rows = [row for row in parsed_rows if row["status"] == "confirmed"]

        pending_due: datetime | None = None
        if pending_rows:
            pending_due = min(row["initiated_at"] for row in pending_rows) + pending_delta

        if failed_rows:
            action = "owner_review_failed_transfer"
            reason = "bound_transfer_reports_failed_status"
        elif pending_rows and pending_due is not None and as_of >= pending_due:
            action = "owner_review_pending_overdue"
            reason = "bound_transfer_exceeded_pending_confirmation_expectation"
        elif pending_rows:
            action = "monitor_pending_confirmation"
            reason = "bound_transfer_within_pending_confirmation_expectation"
        elif confirmed_rows:
            action = "run_revenue_settlement"
            reason = "confirmed_bound_evidence_requires_existing_cash_authority"
        elif as_of >= transfer_due:
            action = "owner_review_missing_transfer"
            reason = "no_bound_transfer_after_initiation_expectation"
        else:
            action = "await_transfer_initiation_window"
            reason = "no_bound_transfer_within_initiation_expectation"

        row = {
            "repo": repo,
            "pr": pr,
            "state": "MERGED",
            "currency": _POLICY["currency"],
            "advertised_amount": _amount_text(item["advertised_amount"]),
            "merged_at": _iso(item["merged_at"]),
            "transfer_initiation_expected_by": _iso(transfer_due),
            "pending_confirmation_expected_by": None if pending_due is None else _iso(pending_due),
            "action": action,
            "reason": reason,
            "bound_history_sha256s": selected_hashes,
            "bound_transfer_count": len(parsed_rows),
            "bound_pending_count": len(pending_rows),
            "bound_confirmed_count": len(confirmed_rows),
            "bound_failed_count": len(failed_rows),
            "bound_amount_rtc": _amount_text(selected_total) if parsed_rows else "0",
            "settlement_followup_url": item["settlement_followup_url"],
            "cash_status": "not_inferred",
            "authority": "owner_review_readiness_only",
        }
        row["evidence_sha256"] = _sha256({
            "wallet": wallet,
            "history_source": history_source,
            "repo": repo,
            "pr": pr,
            "bound_history_sha256s": selected_hashes,
            "policy_sha256": policy_receipt()["policy_sha256"],
        })
        output_items.append(row)

    receipt = {
        "schema_version": _SCHEMA_VERSION,
        "as_of": _iso(as_of),
        "wallet": wallet,
        "history_source": history_source,
        "policy": policy_receipt(),
        "items": output_items,
        "cash_authority": "none",
        "external_action_authority": "none",
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return receipt


def format_summary(receipt: dict[str, Any]) -> str:
    lines = []
    for row in receipt["items"]:
        lines.append(
            f"{row['repo']}#{row['pr']} RTC {row['advertised_amount']} "
            f"{row['action']} ({row['reason']}) cash={row['cash_status']}"
        )
    return "\n".join(lines)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PayoutEscalationInputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: str) -> Any:
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise PayoutEscalationInputError("input JSON must be a regular file")
        if metadata.st_size > _MAX_JSON_BYTES:
            raise PayoutEscalationInputError("input JSON is too large")
        data = os.read(fd, _MAX_JSON_BYTES + 1)
        if len(data) > _MAX_JSON_BYTES:
            raise PayoutEscalationInputError("input JSON is too large")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PayoutEscalationInputError("input JSON must be UTF-8") from exc
    finally:
        os.close(fd)
    return json.loads(text, object_pairs_hook=_reject_duplicate_keys)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.payout_escalation",
        description=(
            "Classify post-merge RTC payout timing evidence for owner review without "
            "recognizing cash or contacting a sponsor."
        ),
    )
    parser.add_argument("closeout", help="revenue_closeout --json output")
    parser.add_argument("bindings", help="operator-owned payout timing binding JSON")
    parser.add_argument("--history", required=True, help="wallet-provenance history capture JSON")
    parser.add_argument("--json", action="store_true", help="emit full JSON receipt")
    args = parser.parse_args(argv)
    try:
        receipt = compile_payout_escalation(
            _load_json(args.closeout),
            _load_json(args.history),
            _load_json(args.bindings),
            as_of=datetime.now(timezone.utc),
        )
    except (OSError, json.JSONDecodeError, PayoutEscalationInputError, PayoutEscalationEvidenceError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(format_summary(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
