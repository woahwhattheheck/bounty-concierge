# SPDX-License-Identifier: MIT
"""Durable cross-run settlement custody for evidence-bound bounty cash.

`concierge.revenue_settlement` proves that exact wallet-history rows can settle
closeout items within one reconciliation. This module adds the missing durable
boundary: once a canonical incoming transaction is bound to one claim, later
independent processes cannot recycle it into another claim or roll that claim
back to an older evidence set.

The registry is bookkeeping/evidence only. It never mutates a wallet/provider,
initiates a payout or transfer, contacts a sponsor, submits a claim, converts
currency, or recognizes accounting revenue.
"""

from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any, Dict, List, Optional, Tuple

from concierge import revenue_settlement as rs


SCHEMA_VERSION = "bounty.settlement-registry/v1"
RECEIPT_SCHEMA_VERSION = "bounty.settlement-registry-receipt/v1"
NETWORK = "rustchain"
_MAX_JSON_BYTES = 4_000_000
_MAX_ITEMS = 10_000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_AMOUNT_RE = re.compile(r"^(?:0|[1-9][0-9]{0,29})(?:\.[0-9]{1,18})?$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_AUTHORITY = {
    "wallet_mutation": False,
    "provider_mutation": False,
    "transfer": False,
    "payout_initiation": False,
    "sponsor_contact": False,
    "claim_submission": False,
    "fx_conversion": False,
    "revenue_recognition": False,
}


class SettlementRegistryError(ValueError):
    """Base class for registry input/evidence failures."""


class SettlementRegistryConflict(SettlementRegistryError):
    """A transaction, claim, generation, or retained state conflicts."""


class SettlementRegistryBusy(SettlementRegistryError):
    """The file-backed registry is locked or awaiting reconciliation."""


class SettlementRegistryAmbiguousCommit(SettlementRegistryError):
    """The visible file may have changed; the lock is intentionally retained."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _strict_pairs(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SettlementRegistryError("JSON contains duplicate object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise SettlementRegistryError("JSON contains non-finite number: " + value)


def strict_json_loads(text: str) -> Any:
    if type(text) is not str:
        raise SettlementRegistryError("JSON source must be text")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_constant,
        )
    except SettlementRegistryError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SettlementRegistryError("registry JSON is malformed") from exc
    _reject_floats(value)
    return value


def _reject_floats(value: Any) -> None:
    if isinstance(value, float):
        raise SettlementRegistryError("registry JSON numbers must not be floating point")
    if isinstance(value, list):
        for child in value:
            _reject_floats(child)
    elif isinstance(value, dict):
        for child in value.values():
            _reject_floats(child)


def _exact_keys(value: Any, expected: set, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise SettlementRegistryError(label + " must be an object")
    actual = set(value)
    if actual != expected:
        raise SettlementRegistryError(label + " fields do not match schema")
    return value


def _clean_text(value: Any, label: str, *, maximum: int = 512) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or value != value.strip()
        or any(not char.isprintable() for char in value)
    ):
        raise SettlementRegistryError(label + " is malformed")
    return value


def _repo(value: Any) -> str:
    text = _clean_text(value, "repo", maximum=200)
    if not _REPO_RE.fullmatch(text):
        raise SettlementRegistryError("repo must be owner/name")
    return text


def _pr(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise SettlementRegistryError("pr must be a positive integer")
    return value


def _sha(value: Any, label: str) -> str:
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        raise SettlementRegistryError(label + " must be lowercase SHA-256")
    return value


def _amount(value: Any, label: str, *, allow_zero: bool) -> Decimal:
    if type(value) is not str or not _AMOUNT_RE.fullmatch(value):
        raise SettlementRegistryError(label + " must be canonical decimal text")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise SettlementRegistryError(label + " is invalid") from exc
    if parsed < 0 or (parsed == 0 and not allow_zero):
        raise SettlementRegistryError(label + " must be positive")
    return parsed


def _amount_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _claim_key(repo: str, pr: int) -> Tuple[str, int]:
    return (repo.casefold(), pr)


def _seal_state(body: Dict[str, Any]) -> Dict[str, Any]:
    sealed = copy.deepcopy(body)
    sealed["state_sha256"] = _sha256_json(body)
    return sealed


def _seal_receipt(body: Dict[str, Any]) -> Dict[str, Any]:
    sealed = copy.deepcopy(body)
    sealed["receipt_sha256"] = _sha256_json(body)
    return sealed


def new_registry(*, wallet: str, history_source: str) -> Dict[str, Any]:
    wallet = _clean_text(wallet, "wallet")
    history_source = _clean_text(history_source, "history_source")
    return _seal_state(
        {
            "schema_version": SCHEMA_VERSION,
            "network": NETWORK,
            "wallet": wallet,
            "history_source": history_source,
            "generation": 0,
            "previous_state_sha256": None,
            "claims": [],
            "transactions": [],
            "authority": copy.deepcopy(_AUTHORITY),
        }
    )


def verify_registry(
    raw: Any,
    *,
    expected_state_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    state = _exact_keys(
        raw,
        {
            "schema_version",
            "network",
            "wallet",
            "history_source",
            "generation",
            "previous_state_sha256",
            "claims",
            "transactions",
            "authority",
            "state_sha256",
        },
        "registry",
    )
    if state["schema_version"] != SCHEMA_VERSION or state["network"] != NETWORK:
        raise SettlementRegistryError("registry schema/network is unsupported")
    _clean_text(state["wallet"], "wallet")
    _clean_text(state["history_source"], "history_source")
    generation = state["generation"]
    if type(generation) is not int or generation < 0:
        raise SettlementRegistryError("generation must be a non-negative integer")
    previous = state["previous_state_sha256"]
    if generation == 0:
        if previous is not None:
            raise SettlementRegistryError("genesis must not have a previous state")
    else:
        _sha(previous, "previous_state_sha256")
    authority = _exact_keys(state["authority"], set(_AUTHORITY), "authority")
    if authority != _AUTHORITY:
        raise SettlementRegistryError("registry authority ceiling changed")
    digest = _sha(state["state_sha256"], "state_sha256")
    body = {
        key: copy.deepcopy(value)
        for key, value in state.items()
        if key != "state_sha256"
    }
    if _sha256_json(body) != digest:
        raise SettlementRegistryError("registry state digest mismatch")
    if expected_state_sha256 is not None:
        _sha(expected_state_sha256, "expected_state_sha256")
        if digest != expected_state_sha256:
            raise SettlementRegistryConflict("registry state does not match expected digest")

    claims_raw = state["claims"]
    txs_raw = state["transactions"]
    if (
        not isinstance(claims_raw, list)
        or len(claims_raw) > _MAX_ITEMS
        or not isinstance(txs_raw, list)
        or len(txs_raw) > _MAX_ITEMS
    ):
        raise SettlementRegistryError("registry collections are malformed or too large")
    if generation == 0:
        if claims_raw or txs_raw:
            raise SettlementRegistryError("genesis registry must be empty")
    else:
        if not claims_raw or not txs_raw:
            raise SettlementRegistryError("non-genesis registry must contain settled evidence")
        if generation < len(claims_raw) or generation > len(txs_raw):
            raise SettlementRegistryError(
                "registry generation is inconsistent with custody history"
            )

    claims: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for claim in claims_raw:
        row = _exact_keys(
            claim,
            {
                "repo",
                "pr",
                "currency",
                "advertised_amount",
                "verified_amount",
                "cash_status",
                "transaction_ids",
            },
            "claim",
        )
        repo = _repo(row["repo"])
        pr = _pr(row["pr"])
        if row["currency"] != "RTC":
            raise SettlementRegistryError("registry v1 only supports RTC")
        advertised = _amount(row["advertised_amount"], "advertised_amount", allow_zero=False)
        verified = _amount(row["verified_amount"], "verified_amount", allow_zero=False)
        if verified > advertised:
            raise SettlementRegistryError("claim verified amount exceeds advertised amount")
        expected_status = "verified_paid" if verified == advertised else "partially_verified"
        if row["cash_status"] != expected_status:
            raise SettlementRegistryError("claim cash status conflicts with amount")
        ids = row["transaction_ids"]
        if (
            not isinstance(ids, list)
            or not ids
            or len(ids) > _MAX_ITEMS
            or ids != sorted(ids)
            or len(set(ids)) != len(ids)
        ):
            raise SettlementRegistryError("claim transaction IDs must be unique sorted list")
        for txid in ids:
            _clean_text(txid, "transaction_id")
        key = _claim_key(repo, pr)
        if key in claims:
            raise SettlementRegistryError("registry contains duplicate claim")
        claims[key] = row

    transactions: Dict[str, Dict[str, Any]] = {}
    per_claim_amounts: Dict[Tuple[str, int], List[Decimal]] = {}
    per_claim_ids: Dict[Tuple[str, int], List[str]] = {}
    for tx in txs_raw:
        row = _exact_keys(
            tx,
            {"transaction_id", "history_sha256", "repo", "pr", "currency", "amount"},
            "transaction",
        )
        txid = _clean_text(row["transaction_id"], "transaction_id")
        _sha(row["history_sha256"], "history_sha256")
        repo = _repo(row["repo"])
        pr = _pr(row["pr"])
        if row["currency"] != "RTC":
            raise SettlementRegistryError("transaction currency must be RTC")
        amount = _amount(row["amount"], "transaction amount", allow_zero=False)
        if txid in transactions:
            raise SettlementRegistryError("registry contains duplicate transaction identity")
        transactions[txid] = row
        key = _claim_key(repo, pr)
        if key not in claims:
            raise SettlementRegistryError("transaction references unknown claim")
        per_claim_amounts.setdefault(key, []).append(amount)
        per_claim_ids.setdefault(key, []).append(txid)

    for key, claim in claims.items():
        ids = sorted(per_claim_ids.get(key, []))
        if ids != claim["transaction_ids"]:
            raise SettlementRegistryError("claim transaction set does not match registry")
        with localcontext() as context:
            context.prec = 128
            total = sum(per_claim_amounts.get(key, []), Decimal("0"))
        if _amount_text(total) != claim["verified_amount"]:
            raise SettlementRegistryError("claim verified amount does not match transactions")

    if claims_raw != sorted(claims_raw, key=lambda row: (row["repo"].casefold(), row["pr"])):
        raise SettlementRegistryError("claims are not canonically ordered")
    if txs_raw != sorted(txs_raw, key=lambda row: row["transaction_id"]):
        raise SettlementRegistryError("transactions are not canonically ordered")
    return copy.deepcopy(state)


def _history_map(history: Any, wallet: str) -> Dict[str, Dict[str, Any]]:
    if not isinstance(history, list) or len(history) > _MAX_ITEMS:
        raise SettlementRegistryError("history must be a bounded list")
    result: Dict[str, Dict[str, Any]] = {}
    for raw in history:
        if not isinstance(raw, dict):
            raise SettlementRegistryError("history row must be an object")
        try:
            fingerprint = rs.history_row_sha256(raw, wallet=wallet)
        except Exception as exc:
            raise SettlementRegistryError("history row could not be fingerprinted") from exc
        _sha(fingerprint, "history_sha256")
        if fingerprint in result:
            raise SettlementRegistryError("history contains duplicate indistinguishable row")
        result[fingerprint] = raw
    return result


def _verified_claim_from_reconciliation(
    closeout_item: Any,
    history: Any,
    binding: Any,
    *,
    wallet: str,
    history_wallet: str,
    history_source: str,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    wallet = _clean_text(wallet, "wallet")
    history_wallet = _clean_text(history_wallet, "history_wallet")
    history_source = _clean_text(history_source, "history_source")
    try:
        rows = rs.reconcile_cash(
            [closeout_item],
            history,
            [binding],
            wallet=wallet,
            history_wallet=history_wallet,
            history_source=history_source,
        )
    except Exception as exc:
        raise SettlementRegistryError("underlying settlement reconciliation failed") from exc
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise SettlementRegistryError("settlement reconciler returned malformed result")
    result = rows[0]
    if result.get("cash_status") not in {"partially_verified", "verified_paid"}:
        raise SettlementRegistryError("registry update requires verified cash evidence")
    if result.get("currency") != "RTC":
        raise SettlementRegistryError("registry v1 only supports RTC")
    if result.get("history_wallet") != wallet or history_wallet != wallet:
        raise SettlementRegistryError("settlement wallet provenance mismatch")
    if result.get("history_source") != history_source:
        raise SettlementRegistryError("settlement history source mismatch")

    repo = _repo(result.get("repo"))
    pr = _pr(result.get("pr"))
    advertised = _amount(result.get("advertised_amount"), "advertised_amount", allow_zero=False)
    verified = _amount(result.get("verified_amount"), "verified_amount", allow_zero=False)
    if verified > advertised:
        raise SettlementRegistryError("verified amount exceeds advertised amount")
    expected_status = "verified_paid" if verified == advertised else "partially_verified"
    if result.get("cash_status") != expected_status:
        raise SettlementRegistryError("cash status conflicts with verified amount")

    history_by_hash = _history_map(history, wallet)
    evidence = result.get("payment_evidence")
    if not isinstance(evidence, list) or not evidence or len(evidence) > _MAX_ITEMS:
        raise SettlementRegistryError("settlement result omitted payment evidence")

    txs: List[Dict[str, Any]] = []
    seen_ids = set()
    seen_hashes = set()
    for item in evidence:
        if not isinstance(item, dict):
            raise SettlementRegistryError("payment evidence entry must be an object")
        fingerprint = _sha(item.get("history_sha256"), "history_sha256")
        if fingerprint in seen_hashes:
            raise SettlementRegistryError("payment evidence repeats one history row")
        seen_hashes.add(fingerprint)
        raw = history_by_hash.get(fingerprint)
        if raw is None:
            raise SettlementRegistryError("payment evidence does not match supplied history")
        if raw.get("type") != "transfer_in":
            raise SettlementRegistryError(
                "durable registry accepts canonical transfer_in evidence only"
            )
        txid = _clean_text(raw.get("tx_hash"), "transaction_id")
        if txid in seen_ids:
            raise SettlementRegistryError("payment evidence repeats transaction identity")
        seen_ids.add(txid)
        amount = _amount(item.get("amount_rtc"), "payment evidence amount", allow_zero=False)
        txs.append(
            {
                "transaction_id": txid,
                "history_sha256": fingerprint,
                "repo": repo,
                "pr": pr,
                "currency": "RTC",
                "amount": _amount_text(amount),
            }
        )

    txs.sort(key=lambda row: row["transaction_id"])
    with localcontext() as context:
        context.prec = 128
        total = sum(
            (_amount(row["amount"], "transaction amount", allow_zero=False) for row in txs),
            Decimal("0"),
        )
    if _amount_text(total) != _amount_text(verified):
        raise SettlementRegistryError("payment evidence amount does not match reconciliation")

    claim = {
        "repo": repo,
        "pr": pr,
        "currency": "RTC",
        "advertised_amount": _amount_text(advertised),
        "verified_amount": _amount_text(verified),
        "cash_status": expected_status,
        "transaction_ids": [row["transaction_id"] for row in txs],
    }
    return claim, txs


def _apply_verified_claim(
    registry: Dict[str, Any],
    claim: Dict[str, Any],
    txs: List[Dict[str, Any]],
    *,
    expected_state_sha256: str,
) -> Tuple[Dict[str, Any], bool]:
    current = verify_registry(registry, expected_state_sha256=expected_state_sha256)
    key = _claim_key(claim["repo"], claim["pr"])
    claims = {
        _claim_key(row["repo"], row["pr"]): copy.deepcopy(row)
        for row in current["claims"]
    }
    transactions = {
        row["transaction_id"]: copy.deepcopy(row) for row in current["transactions"]
    }
    incoming_ids = {row["transaction_id"] for row in txs}
    if len(incoming_ids) != len(txs):
        raise SettlementRegistryError("incoming transaction identities are duplicated")
    existing_claim = claims.get(key)
    if existing_claim is not None:
        if (
            existing_claim["currency"] != claim["currency"]
            or existing_claim["advertised_amount"] != claim["advertised_amount"]
            or existing_claim["repo"] != claim["repo"]
            or existing_claim["pr"] != claim["pr"]
        ):
            raise SettlementRegistryConflict("claim identity/economics changed")
        old_ids = set(existing_claim["transaction_ids"])
        if not old_ids.issubset(incoming_ids):
            raise SettlementRegistryConflict("claim evidence rolled back or omitted")
    for tx in txs:
        prior = transactions.get(tx["transaction_id"])
        if prior is not None and prior != tx:
            raise SettlementRegistryConflict(
                "transaction identity is already bound to different evidence or claim"
            )
    if existing_claim == claim and all(
        transactions.get(tx["transaction_id"]) == tx for tx in txs
    ):
        return current, False

    claims[key] = copy.deepcopy(claim)
    for tx in txs:
        transactions[tx["transaction_id"]] = copy.deepcopy(tx)
    next_body = {
        "schema_version": SCHEMA_VERSION,
        "network": NETWORK,
        "wallet": current["wallet"],
        "history_source": current["history_source"],
        "generation": current["generation"] + 1,
        "previous_state_sha256": current["state_sha256"],
        "claims": sorted(claims.values(), key=lambda row: (row["repo"].casefold(), row["pr"])),
        "transactions": sorted(transactions.values(), key=lambda row: row["transaction_id"]),
        "authority": copy.deepcopy(_AUTHORITY),
    }
    next_state = _seal_state(next_body)
    verify_registry(next_state)
    return next_state, True


def reconcile_and_apply(
    registry: Dict[str, Any],
    closeout_item: Any,
    history: Any,
    binding: Any,
    *,
    wallet: str,
    history_wallet: str,
    history_source: str,
    expected_state_sha256: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    current = verify_registry(registry, expected_state_sha256=expected_state_sha256)
    if current["wallet"] != wallet:
        raise SettlementRegistryConflict("registry wallet does not match requested wallet")
    if current["history_source"] != history_source:
        raise SettlementRegistryConflict(
            "registry history source does not match requested source"
        )
    claim, txs = _verified_claim_from_reconciliation(
        closeout_item,
        history,
        binding,
        wallet=wallet,
        history_wallet=history_wallet,
        history_source=history_source,
    )
    next_state, changed = _apply_verified_claim(
        current,
        claim,
        txs,
        expected_state_sha256=current["state_sha256"],
    )
    receipt = _seal_receipt(
        {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "network": NETWORK,
            "wallet": current["wallet"],
            "history_source": current["history_source"],
            "previous_state_sha256": current["state_sha256"],
            "state_sha256": next_state["state_sha256"],
            "generation": next_state["generation"],
            "changed": changed,
            "repo": claim["repo"],
            "pr": claim["pr"],
            "currency": claim["currency"],
            "verified_amount": claim["verified_amount"],
            "cash_status": claim["cash_status"],
            "transaction_ids": claim["transaction_ids"],
            "authority": copy.deepcopy(_AUTHORITY),
        }
    )
    verify_receipt(receipt, registry=next_state)
    return next_state, receipt


def verify_receipt(receipt: Any, *, registry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    row = _exact_keys(
        receipt,
        {
            "schema_version",
            "network",
            "wallet",
            "history_source",
            "previous_state_sha256",
            "state_sha256",
            "generation",
            "changed",
            "repo",
            "pr",
            "currency",
            "verified_amount",
            "cash_status",
            "transaction_ids",
            "authority",
            "receipt_sha256",
        },
        "receipt",
    )
    if row["schema_version"] != RECEIPT_SCHEMA_VERSION or row["network"] != NETWORK:
        raise SettlementRegistryError("receipt schema/network is unsupported")
    _clean_text(row["wallet"], "wallet")
    _clean_text(row["history_source"], "history_source")
    _sha(row["previous_state_sha256"], "previous_state_sha256")
    state_sha = _sha(row["state_sha256"], "state_sha256")
    if type(row["generation"]) is not int or row["generation"] < 0:
        raise SettlementRegistryError("receipt generation is invalid")
    if type(row["changed"]) is not bool:
        raise SettlementRegistryError("receipt changed flag must be boolean")
    repo = _repo(row["repo"])
    pr = _pr(row["pr"])
    if row["currency"] != "RTC":
        raise SettlementRegistryError("receipt currency must be RTC")
    _amount(row["verified_amount"], "verified_amount", allow_zero=False)
    if row["cash_status"] not in {"partially_verified", "verified_paid"}:
        raise SettlementRegistryError("receipt cash status is invalid")
    ids = row["transaction_ids"]
    if not isinstance(ids, list) or not ids or ids != sorted(ids) or len(set(ids)) != len(ids):
        raise SettlementRegistryError("receipt transaction IDs are invalid")
    for txid in ids:
        _clean_text(txid, "transaction_id")
    if _exact_keys(row["authority"], set(_AUTHORITY), "receipt authority") != _AUTHORITY:
        raise SettlementRegistryError("receipt authority ceiling changed")
    digest = _sha(row["receipt_sha256"], "receipt_sha256")
    body = {
        key: copy.deepcopy(value)
        for key, value in row.items()
        if key != "receipt_sha256"
    }
    if _sha256_json(body) != digest:
        raise SettlementRegistryError("receipt digest mismatch")

    if registry is not None:
        state = verify_registry(registry, expected_state_sha256=state_sha)
        if state["wallet"] != row["wallet"] or state["history_source"] != row["history_source"]:
            raise SettlementRegistryError("receipt registry provenance mismatch")
        if state["generation"] != row["generation"]:
            raise SettlementRegistryError("receipt generation does not match registry")
        claim = next(
            (
                item
                for item in state["claims"]
                if _claim_key(item["repo"], item["pr"]) == _claim_key(repo, pr)
            ),
            None,
        )
        if claim is None:
            raise SettlementRegistryError("receipt claim is absent from registry")
        for field in ("currency", "verified_amount", "cash_status", "transaction_ids"):
            if claim[field] != row[field]:
                raise SettlementRegistryError("receipt claim does not match registry")
        if row["changed"]:
            if state["previous_state_sha256"] != row["previous_state_sha256"]:
                raise SettlementRegistryError("receipt predecessor does not match registry")
        elif row["previous_state_sha256"] != row["state_sha256"]:
            raise SettlementRegistryError("idempotent receipt must preserve state digest")
    return copy.deepcopy(row)


def _lock_path(path: Path) -> Path:
    return Path(str(path) + ".lock")


def _acquire_lock(path: Path, *, status: str = "writing") -> Path:
    if status not in {"writing", "reading"}:
        raise SettlementRegistryError("lock status is invalid")
    lock = _lock_path(path)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise SettlementRegistryBusy(
            "registry is locked or awaiting operator reconciliation"
        ) from exc
    try:
        payload = canonical_json(
            {
                "schema_version": "bounty.settlement-registry-lock/v1",
                "registry": path.name,
                "status": status,
                "candidate_state_sha256": None,
            }
        ).encode("utf-8")
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    return lock


def _mark_ambiguous(lock: Path, state_sha256: str) -> None:
    _sha(state_sha256, "candidate_state_sha256")
    payload = (
        canonical_json(
            {
                "schema_version": "bounty.settlement-registry-lock/v1",
                "registry": (
                    lock.name[: -len(".lock")]
                    if lock.name.endswith(".lock")
                    else lock.name
                ),
                "status": "ambiguous",
                "candidate_state_sha256": state_sha256,
            }
        )
        + "\n"
    ).encode("utf-8")
    try:
        fd = os.open(str(lock), os.O_WRONLY | os.O_TRUNC)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        return


def _release_lock(
    lock: Path,
    *,
    after_commit: bool,
    committed_state_sha256: Optional[str] = None,
) -> None:
    try:
        lock.unlink()
    except OSError as exc:
        if after_commit:
            if committed_state_sha256 is not None:
                _mark_ambiguous(lock, committed_state_sha256)
            raise SettlementRegistryAmbiguousCommit(
                "registry committed but lock release failed; reconcile before reuse"
            ) from exc
        raise SettlementRegistryBusy("registry lock could not be released") from exc


def _read_state_bytes(path: Path) -> Dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SettlementRegistryError("registry file is unavailable") from exc
    if size > _MAX_JSON_BYTES:
        raise SettlementRegistryError("registry file is too large")
    try:
        data = path.read_bytes()
        text = data.decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as exc:
        raise SettlementRegistryError("registry file is unreadable UTF-8") from exc
    return verify_registry(strict_json_loads(text))


def load_registry_file(path: Any) -> Dict[str, Any]:
    target = Path(path)
    lock = _acquire_lock(target, status="reading")
    try:
        state = _read_state_bytes(target)
        _release_lock(lock, after_commit=False)
        return state
    except Exception:
        if lock.exists():
            try:
                _release_lock(lock, after_commit=False)
            except SettlementRegistryBusy:
                pass
        raise


def audit_registry_file(path: Any) -> Dict[str, Any]:
    target = Path(path)
    state = _read_state_bytes(target)
    return {
        "live_authority": False,
        "lock_present": _lock_path(target).exists(),
        "state": state,
    }


def _fsync_parent(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(str(path.parent), flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_state_under_lock(path: Path, state: Dict[str, Any]) -> None:
    verify_registry(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / (
        "." + path.name + ".tmp." + str(os.getpid()) + "." + secrets.token_hex(8)
    )
    committed = False
    try:
        payload = (canonical_json(state) + "\n").encode("utf-8")
        fd = os.open(str(temp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        os.replace(str(temp), str(path))
        committed = True
        _fsync_parent(path)
        observed = _read_state_bytes(path)
        if observed["state_sha256"] != state["state_sha256"]:
            raise SettlementRegistryError("registry readback digest changed after commit")
    except Exception as exc:
        if not committed:
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass
            raise
        raise SettlementRegistryAmbiguousCommit(
            "registry became visible before durable/readback completion; lock retained"
        ) from exc


def initialize_registry_file(
    path: Any,
    *,
    wallet: str,
    history_source: str,
) -> Dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = _acquire_lock(target)
    committed = False
    try:
        if target.exists():
            raise SettlementRegistryConflict("registry file already exists")
        state = new_registry(wallet=wallet, history_source=history_source)
        _write_state_under_lock(target, state)
        committed = True
        _release_lock(
            lock,
            after_commit=True,
            committed_state_sha256=state["state_sha256"],
        )
        return state
    except SettlementRegistryAmbiguousCommit:
        if lock.exists() and target.exists():
            try:
                candidate = _read_state_bytes(target)["state_sha256"]
            except SettlementRegistryError:
                candidate = state["state_sha256"] if "state" in locals() else None
            if candidate is not None:
                _mark_ambiguous(lock, candidate)
        raise
    except Exception:
        if not committed and lock.exists():
            _release_lock(lock, after_commit=False)
        raise


def commit_claim_file(
    path: Any,
    closeout_item: Any,
    history: Any,
    binding: Any,
    *,
    wallet: str,
    history_wallet: str,
    history_source: str,
    expected_state_sha256: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    target = Path(path)
    lock = _acquire_lock(target)
    committed = False
    try:
        current = _read_state_bytes(target)
        next_state, receipt = reconcile_and_apply(
            current,
            closeout_item,
            history,
            binding,
            wallet=wallet,
            history_wallet=history_wallet,
            history_source=history_source,
            expected_state_sha256=expected_state_sha256,
        )
        if next_state["state_sha256"] == current["state_sha256"]:
            _release_lock(lock, after_commit=False)
            return next_state, receipt
        _write_state_under_lock(target, next_state)
        committed = True
        _release_lock(
            lock,
            after_commit=True,
            committed_state_sha256=next_state["state_sha256"],
        )
        return next_state, receipt
    except SettlementRegistryAmbiguousCommit:
        if lock.exists() and target.exists():
            try:
                candidate = _read_state_bytes(target)["state_sha256"]
            except SettlementRegistryError:
                candidate = (
                    next_state["state_sha256"] if "next_state" in locals() else None
                )
            if candidate is not None:
                _mark_ambiguous(lock, candidate)
        raise
    except Exception:
        if not committed and lock.exists():
            _release_lock(lock, after_commit=False)
        raise


def _read_lock_marker(lock: Path) -> Dict[str, Any]:
    try:
        raw = lock.read_bytes()
        text = raw.decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as exc:
        raise SettlementRegistryBusy("registry lock is unreadable") from exc
    marker = strict_json_loads(text)
    row = _exact_keys(
        marker,
        {"schema_version", "registry", "status", "candidate_state_sha256"},
        "registry lock",
    )
    if row["schema_version"] != "bounty.settlement-registry-lock/v1":
        raise SettlementRegistryBusy("registry lock schema is unsupported")
    _clean_text(row["registry"], "lock registry")
    if row["status"] not in {"writing", "reading", "ambiguous"}:
        raise SettlementRegistryBusy("registry lock status is invalid")
    if row["status"] in {"writing", "reading"}:
        if row["candidate_state_sha256"] is not None:
            raise SettlementRegistryBusy("active lock carries invalid candidate digest")
    else:
        _sha(row["candidate_state_sha256"], "candidate_state_sha256")
    return row


def clear_reconciled_lock(
    path: Any,
    *,
    expected_state_sha256: str,
) -> Dict[str, Any]:
    target = Path(path)
    lock = _lock_path(target)
    if not lock.exists():
        raise SettlementRegistryConflict("registry has no reconciliation lock")
    marker = _read_lock_marker(lock)
    if marker["status"] != "ambiguous":
        raise SettlementRegistryBusy(
            "registry lock is not an ambiguity marker; manual recovery required"
        )
    state = _read_state_bytes(target)
    verify_registry(state, expected_state_sha256=expected_state_sha256)
    if marker["candidate_state_sha256"] != expected_state_sha256:
        raise SettlementRegistryConflict(
            "ambiguity marker does not match accepted registry state"
        )
    try:
        lock.unlink()
    except OSError as exc:
        raise SettlementRegistryBusy("reconciliation lock could not be cleared") from exc
    return state
