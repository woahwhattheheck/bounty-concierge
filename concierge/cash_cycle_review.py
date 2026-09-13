# SPDX-License-Identifier: MIT
"""Evidence-bound cash-cycle review for merged RTC bounty work.

The landed payout-escalation compiler is the source authority gate. This module
only derives factual transfer-timing observations for owner review; it never
infers that a repository owner is the payer/sponsor, contacts anyone, changes
opportunity ranking, moves money, or recognizes revenue.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import stat
from typing import Any

from concierge import payout_escalation as _payout

_SCHEMA_VERSION = 1
_PRODUCT = "bounty-cash-cycle-review/v1"
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_ITEMS = 10_000
_MAX_POLICY_SAMPLES = 10_000
_MAX_REVIEW_LAG_HOURS = 24 * 365 * 5
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class CashCycleInputError(ValueError):
    """Malformed or ambiguous cash-cycle input."""


class CashCycleEvidenceError(RuntimeError):
    """Upstream evidence cannot support one cash-cycle observation."""


def _canonical_json(value: Any) -> bytes:
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CashCycleInputError("value is not canonical JSON") from exc
    if len(data) > _MAX_JSON_BYTES:
        raise CashCycleInputError("canonical JSON value is too large")
    return data


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _exact_int(value: Any, *, field: str, minimum: int | None = None,
               maximum: int | None = None) -> int:
    if type(value) is not int:
        raise CashCycleInputError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise CashCycleInputError(f"{field} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise CashCycleInputError(f"{field} must be <= {maximum}")
    return value


def _parse_utc(value: Any, *, field: str) -> datetime:
    if type(value) is not str or not _UTC_RE.fullmatch(value):
        raise CashCycleInputError(
            f"{field} must be canonical UTC with at most microsecond precision")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CashCycleInputError(f"{field} must be canonical UTC") from exc
    return parsed.astimezone(timezone.utc)


def _parse_history_time(value: Any, *, field: str) -> datetime:
    if isinstance(value, bool):
        raise CashCycleEvidenceError(
            f"{field} must be canonical UTC text or Unix seconds")
    if type(value) is int:
        if value < 0 or value > 253402300799:
            raise CashCycleEvidenceError(f"{field} Unix seconds are out of range")
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise CashCycleEvidenceError(
                f"{field} Unix seconds are out of range") from exc
    try:
        return _parse_utc(value, field=field)
    except CashCycleInputError as exc:
        raise CashCycleEvidenceError(str(exc)) from exc


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _policy(raw: Any) -> dict[str, Any]:
    expected = {"schema_version", "minimum_confirmed_samples", "review_lag_hours"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise CashCycleInputError(
            "policy must contain only schema_version, minimum_confirmed_samples, "
            "and review_lag_hours")
    if raw.get("schema_version") != 1:
        raise CashCycleInputError("policy schema_version must be 1")
    minimum = _exact_int(raw.get("minimum_confirmed_samples"),
                         field="minimum_confirmed_samples", minimum=1,
                         maximum=_MAX_POLICY_SAMPLES)
    lag = _exact_int(raw.get("review_lag_hours"), field="review_lag_hours",
                     minimum=1, maximum=_MAX_REVIEW_LAG_HOURS)
    normalized = {"schema_version": 1, "minimum_confirmed_samples": minimum,
                  "review_lag_hours": lag}
    return {**normalized, "policy_sha256": _sha256(normalized)}


def _history_rows(raw: Any, *, expected_wallet: str) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        raise CashCycleInputError("history capture must be an object")
    if set(raw) == {"ok", "miner_id", "transactions", "total"}:
        if raw.get("ok") is not True:
            raise CashCycleEvidenceError("canonical wallet history did not report ok=true")
        if raw.get("miner_id") != expected_wallet:
            raise CashCycleEvidenceError("history wallet does not match payout receipt")
        rows = raw.get("transactions")
        total = raw.get("total")
        if type(total) is not int or total < 0:
            raise CashCycleInputError("canonical history total must be non-negative integer")
        if not isinstance(rows, list) or len(rows) > _MAX_ITEMS:
            raise CashCycleInputError("canonical history transactions must be bounded")
        if total != len(rows):
            raise CashCycleEvidenceError(
                "canonical history total does not match captured transactions")
    elif set(raw) == {"schema_version", "source", "wallet", "items"}:
        if raw.get("schema_version") != 1 or raw.get("source") != "rustchain_wallet_history":
            raise CashCycleInputError("normalized history authority fields are invalid")
        if raw.get("wallet") != expected_wallet:
            raise CashCycleEvidenceError("history wallet does not match payout receipt")
        rows = raw.get("items")
        if not isinstance(rows, list) or len(rows) > _MAX_ITEMS:
            raise CashCycleInputError("normalized history items must be bounded")
    else:
        raise CashCycleInputError(
            "history capture must preserve exact RustChain wallet provenance")
    if any(not isinstance(row, dict) for row in rows):
        raise CashCycleInputError("wallet history rows must be objects")
    return rows


def _row_time(row: dict[str, Any]) -> datetime:
    values = [row[key] for key in ("timestamp", "created_at") if key in row]
    if not values:
        raise CashCycleEvidenceError(
            "confirmed bound transfer omitted transaction/initiation timestamp")
    parsed = [_parse_history_time(value, field="wallet history timestamp")
              for value in values]
    if len(set(parsed)) != 1:
        raise CashCycleEvidenceError(
            "wallet history row has conflicting transaction timestamps")
    return parsed[0]


def _confirmed_status(row: dict[str, Any]) -> bool:
    status = row.get("status", "confirmed")
    if type(status) is not str or status != status.strip():
        raise CashCycleEvidenceError("wallet history row has malformed status")
    return status == "confirmed"


def _repo_pr(raw: Any) -> tuple[str, int]:
    if not isinstance(raw, dict):
        raise CashCycleInputError("payout receipt item must be an object")
    repo, pr = raw.get("repo"), raw.get("pr")
    if type(repo) is not str or not _REPO_RE.fullmatch(repo):
        raise CashCycleInputError("payout receipt repo must be owner/name")
    owner, name = repo.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise CashCycleInputError("payout receipt repo contains dot path segment")
    _exact_int(pr, field="payout receipt pr", minimum=1)
    return repo.casefold(), pr


def _median_text(values: list[int]) -> tuple[str, int]:
    """Return exact median as text and doubled median for threshold comparison."""
    ordered = sorted(values)
    if not ordered:
        raise CashCycleInputError("median requires at least one value")
    n = len(ordered)
    doubled = (2 * ordered[n // 2] if n % 2
               else ordered[n // 2 - 1] + ordered[n // 2])
    return (str(doubled // 2) if doubled % 2 == 0 else f"{doubled // 2}.5",
            doubled)


def compile_cash_cycle_review(closeout_payload: Any, history_payload: Any,
                              bindings_payload: Any, policy_payload: Any, *,
                              evaluated_at: datetime) -> dict[str, Any]:
    """Compile repository-level confirmed-transfer lag observations."""
    if (not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None
            or evaluated_at.utcoffset() is None):
        raise CashCycleInputError("evaluated_at must be timezone-aware")
    evaluated_at = evaluated_at.astimezone(timezone.utc)
    policy = _policy(policy_payload)

    # Source authority gate. Preserve upstream error classes/messages.
    payout = _payout.compile_payout_escalation(
        closeout_payload, history_payload, bindings_payload, as_of=evaluated_at)
    if not isinstance(payout, dict):
        raise CashCycleEvidenceError("payout escalation compiler returned malformed receipt")
    payout_sha = payout.get("receipt_sha256")
    if type(payout_sha) is not str or not _SHA256_RE.fullmatch(payout_sha):
        raise CashCycleEvidenceError(
            "payout escalation receipt omitted valid receipt_sha256")
    wallet = payout.get("wallet")
    if type(wallet) is not str or not wallet or wallet != wallet.strip():
        raise CashCycleEvidenceError("payout escalation receipt omitted wallet provenance")
    if (payout.get("cash_authority") != "none"
            or payout.get("external_action_authority") != "none"):
        raise CashCycleEvidenceError(
            "payout escalation authority ceiling changed unexpectedly")
    items = payout.get("items")
    if not isinstance(items, list) or len(items) > _MAX_ITEMS:
        raise CashCycleEvidenceError("payout escalation items must be bounded")

    rows = _history_rows(history_payload, expected_wallet=wallet)
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        fingerprint = _payout.history_row_sha256(row, wallet=wallet)
        if type(fingerprint) is not str or not _SHA256_RE.fullmatch(fingerprint):
            raise CashCycleEvidenceError("history fingerprint is not lowercase SHA-256")
        if fingerprint in indexed:
            raise CashCycleEvidenceError("history contains duplicate indistinguishable rows")
        indexed[fingerprint] = row

    observations: list[dict[str, Any]] = []
    seen_identity: set[tuple[str, int]] = set()
    observed_by_repo: dict[str, set[int]] = {}
    for raw in items:
        repo, pr = _repo_pr(raw)
        identity = (repo, pr)
        if identity in seen_identity:
            raise CashCycleEvidenceError(
                "payout receipt repeats one repository/PR identity")
        seen_identity.add(identity)
        if raw.get("state") != "MERGED" or raw.get("currency") != "RTC":
            raise CashCycleEvidenceError(
                "payout observation lost merged RTC identity")
        observed_by_repo.setdefault(repo, set()).add(pr)
        if raw.get("action") != "run_revenue_settlement":
            continue
        merged_at = _parse_utc(raw.get("merged_at"), field=f"{repo}#{pr}.merged_at")
        if merged_at > evaluated_at:
            raise CashCycleEvidenceError("payout observation merge time is in the future")
        selected = raw.get("bound_history_sha256s")
        if not isinstance(selected, list) or not selected:
            raise CashCycleEvidenceError(
                "run_revenue_settlement observation requires confirmed bound history")
        if len(selected) > 100 or len(set(selected)) != len(selected):
            raise CashCycleEvidenceError("bound history selection is invalid")
        times: list[tuple[datetime, str]] = []
        for fingerprint in selected:
            if type(fingerprint) is not str or not _SHA256_RE.fullmatch(fingerprint):
                raise CashCycleEvidenceError("bound history fingerprint is invalid")
            row = indexed.get(fingerprint)
            if row is None:
                raise CashCycleEvidenceError(
                    "bound history fingerprint is absent from capture")
            if not _confirmed_status(row):
                raise CashCycleEvidenceError(
                    "run_revenue_settlement observation included non-confirmed history")
            observed = _row_time(row)
            if observed < merged_at:
                raise CashCycleEvidenceError("confirmed transfer predates merge")
            if observed > evaluated_at:
                raise CashCycleEvidenceError(
                    "confirmed transfer timestamp is in the future")
            times.append((observed, fingerprint))
        times.sort(key=lambda pair: (pair[0], pair[1]))
        first_at, last_at = times[0][0], times[-1][0]
        observations.append({
            "repo": repo,
            "pr": pr,
            "merged_at": _iso(merged_at),
            "first_confirmed_transfer_at": _iso(first_at),
            "last_confirmed_transfer_at": _iso(last_at),
            "first_confirmed_transfer_lag_seconds": int((first_at - merged_at).total_seconds()),
            "last_confirmed_transfer_lag_seconds": int((last_at - merged_at).total_seconds()),
            "confirmed_transfer_evidence_count": len(times),
            "bound_history_sha256s": sorted(selected),
            "cash_claim": "none_from_cash_cycle_review",
        })

    observations.sort(key=lambda row: (row["repo"], row["pr"]))
    repo_groups: dict[str, list[dict[str, Any]]] = {}
    for row in observations:
        repo_groups.setdefault(row["repo"], []).append(row)

    repositories: list[dict[str, Any]] = []
    threshold_seconds = policy["review_lag_hours"] * 3600
    for repo in sorted(observed_by_repo):
        group = repo_groups.get(repo, [])
        first_values = [row["first_confirmed_transfer_lag_seconds"] for row in group]
        last_values = [row["last_confirmed_transfer_lag_seconds"] for row in group]
        median_last = None
        median_last_x2 = None
        if last_values:
            median_last, median_last_x2 = _median_text(last_values)
        if len(group) < policy["minimum_confirmed_samples"]:
            state = "INSUFFICIENT_CONFIRMED_HISTORY"
        elif median_last_x2 is not None and median_last_x2 >= 2 * threshold_seconds:
            state = "READY_FOR_OWNER_CASH_CYCLE_REVIEW"
        else:
            state = "OBSERVED_NO_REVIEW_TRIGGER"
        repositories.append({
            "repo": repo,
            "state": state,
            "observed_merged_item_count": len(observed_by_repo[repo]),
            "confirmed_sample_count": len(group),
            "minimum_confirmed_samples": policy["minimum_confirmed_samples"],
            "review_lag_hours": policy["review_lag_hours"],
            "min_first_confirmed_transfer_lag_seconds": min(first_values) if first_values else None,
            "min_last_confirmed_transfer_lag_seconds": min(last_values) if last_values else None,
            "median_last_confirmed_transfer_lag_seconds": median_last,
            "max_last_confirmed_transfer_lag_seconds": max(last_values) if last_values else None,
            "source_prs": sorted(observed_by_repo[repo]),
        })

    source = {
        "closeout_sha256": _sha256(closeout_payload),
        "history_capture_sha256": _sha256(history_payload),
        "bindings_sha256": _sha256(bindings_payload),
        "payout_escalation_receipt_sha256": payout_sha,
    }
    receipt = {
        "schema_version": _SCHEMA_VERSION,
        "product": _PRODUCT,
        "evaluated_at": _iso(evaluated_at),
        "source": source,
        "policy": policy,
        "observations": observations,
        "repositories": repositories,
        "authority": {
            "owner_review_only": True,
            "payer_or_sponsor_identity_inference": False,
            "sponsor_or_maintainer_contact": False,
            "opportunity_rank_mutation": False,
            "wallet_or_provider_mutation": False,
            "payout_or_transfer_initiation": False,
            "debt_or_receivable_assertion": False,
            "cash_recognition": False,
            "revenue_recognition": False,
        },
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return receipt


def verify_cash_cycle_review(receipt: Any, closeout_payload: Any,
                             history_payload: Any, bindings_payload: Any,
                             policy_payload: Any, *,
                             verified_at: datetime) -> bool:
    """Recompute a historical receipt and reject verifier-time rollback."""
    if not isinstance(receipt, dict):
        return False
    if (not isinstance(verified_at, datetime) or verified_at.tzinfo is None
            or verified_at.utcoffset() is None):
        raise CashCycleInputError("verified_at must be timezone-aware")
    try:
        evaluated_at = _parse_utc(receipt.get("evaluated_at"),
                                  field="receipt.evaluated_at")
    except CashCycleInputError:
        return False
    if verified_at.astimezone(timezone.utc) < evaluated_at:
        return False
    try:
        expected = compile_cash_cycle_review(
            closeout_payload, history_payload, bindings_payload, policy_payload,
            evaluated_at=evaluated_at)
    except Exception:
        return False
    return _canonical_json(receipt) == _canonical_json(expected)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CashCycleInputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise CashCycleInputError(f"non-standard JSON numeric constant: {value}")


def _load_json(path: str) -> Any:
    flags = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise CashCycleInputError(f"cannot open input JSON: {path}") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise CashCycleInputError("input JSON must be a regular file")
        if metadata.st_size > _MAX_JSON_BYTES:
            raise CashCycleInputError("input JSON is too large")
        data = os.read(fd, _MAX_JSON_BYTES + 1)
        if len(data) > _MAX_JSON_BYTES:
            raise CashCycleInputError("input JSON is too large")
    finally:
        os.close(fd)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CashCycleInputError("input JSON must be UTF-8") from exc
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys,
                          parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise CashCycleInputError(f"input JSON is invalid: {path}") from exc


def _write_exclusive(path: str, payload: Any) -> None:
    data = _canonical_json(payload) + b"\n"
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise CashCycleInputError("output already exists") from exc
    except OSError as exc:
        raise CashCycleInputError("cannot create output") from exc
    try:
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:
                raise CashCycleInputError("short write while publishing output")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.cash_cycle_review",
        description=("Compile or verify evidence-bound repository cash-cycle "
                     "owner-review receipts."))
    sub = parser.add_subparsers(dest="command", required=True)
    cp = sub.add_parser("compile")
    for name in ("closeout", "history", "bindings", "policy", "output"):
        cp.add_argument(name)
    vp = sub.add_parser("verify")
    for name in ("receipt", "closeout", "history", "bindings", "policy"):
        vp.add_argument(name)
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        closeout, history = _load_json(args.closeout), _load_json(args.history)
        bindings, policy = _load_json(args.bindings), _load_json(args.policy)
        if args.command == "compile":
            receipt = compile_cash_cycle_review(
                closeout, history, bindings, policy, evaluated_at=now)
            _write_exclusive(args.output, receipt)
            return 0
        receipt = _load_json(args.receipt)
        valid = verify_cash_cycle_review(
            receipt, closeout, history, bindings, policy, verified_at=now)
        print(json.dumps({"valid": valid}, separators=(",", ":"), sort_keys=True))
        return 0 if valid else 3
    except (CashCycleInputError, CashCycleEvidenceError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
