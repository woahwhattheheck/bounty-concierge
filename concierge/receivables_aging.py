# SPDX-License-Identifier: MIT
"""Evidence-bound owner review for merged work awaiting verified settlement.

This compatibility entrypoint intentionally does *not* claim that a merge or an
advertised bounty creates a receivable, debt, payable obligation, or earned
revenue.  It re-runs current GitHub closeout and canonical wallet settlement,
then reports only what those sources can prove:

* the work is currently merged;
* a configured RTC amount was advertised;
* some amount may be verified by canonical wallet evidence; and
* the remainder is merely advertised value not yet verified in that wallet.

A configured settlement-followup URL is route metadata only.  It never proves
that contact occurred.  All outputs are owner-review states; no outbound action
is performed or authorized here.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from concierge import revenue_closeout, revenue_settlement


_RECEIPT_SCHEMA = "bounty-merged-work-settlement-review/v1"
_POLICY_SCHEMA = "bounty-merged-work-settlement-review-policy/v1"
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_ITEMS = 10_000
_MAX_THRESHOLD_HOURS = 24 * 365 * 10
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_POLICY_KEYS = frozenset(
    {"schema", "version", "review_after_hours", "aged_review_after_hours"}
)
_ACTION_PRIORITY = {
    "OWNER_AGED_UNSETTLED_REVIEW": 0,
    "OWNER_UNSETTLED_REVIEW": 1,
    "MONITOR_UNSETTLED": 2,
    "SETTLED_VERIFIED": 3,
}
_ALLOWED_CASH_STATUS = frozenset(
    {"verified_paid", "partially_verified", "not_inferred"}
)
_ALLOWED_CLOSEOUT_STATES = frozenset(
    {"OPEN", "CLOSED_UNMERGED", "MERGED", "HEAD_MOVED"}
)


class ReceivablesInputError(ValueError):
    """Raised when review inputs or joined authority are structurally unsafe."""


def _trusted_utc_now() -> datetime:
    """Return verifier-owned current UTC; callers cannot backdate aging."""
    return datetime.now(timezone.utc)


def _canonical_json(value: Any) -> bytes:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReceivablesInputError("value is not canonical JSON") from exc
    if len(payload) > _MAX_JSON_BYTES:
        raise ReceivablesInputError("canonical JSON value is too large")
    return payload


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _strict_keys(value: Any, expected: Iterable[str], name: str) -> Dict[str, Any]:
    if type(value) is not dict:
        raise ReceivablesInputError(f"{name} must be an object")
    expected_set = set(expected)
    actual = set(value)
    if actual != expected_set:
        missing = sorted(expected_set - actual)
        extra = sorted(actual - expected_set)
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if extra:
            detail.append("extra=" + ",".join(extra))
        suffix = ": " + "; ".join(detail) if detail else ""
        raise ReceivablesInputError(f"{name} must have exact keys{suffix}")
    return value


def _positive_int(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or value <= 0 or value > maximum:
        raise ReceivablesInputError(f"{field} must be an integer in 1..{maximum}")
    return value


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ReceivablesInputError(f"{field} must be an exact decimal")
    if isinstance(value, int):
        if value.bit_length() > 256:
            raise ReceivablesInputError(f"{field} is outside bounded decimal form")
        source = str(value)
    else:
        source = str(value)
    if len(source) > 96 or "e" in source.lower() or source.startswith("+"):
        raise ReceivablesInputError(f"{field} must use bounded fixed-point form")
    try:
        result = Decimal(source)
    except (InvalidOperation, ValueError) as exc:
        raise ReceivablesInputError(f"{field} must be a finite decimal") from exc
    exponent = result.as_tuple().exponent
    if (
        not result.is_finite()
        or len(result.as_tuple().digits) > 60
        or not isinstance(exponent, int)
        or abs(exponent) > 36
        or result < 0
    ):
        raise ReceivablesInputError(
            f"{field} is outside bounded nonnegative decimal form"
        )
    return result


def _amount_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _utc(value: Any, field: str) -> datetime:
    if type(value) is not str or not _UTC_RE.fullmatch(value):
        raise ReceivablesInputError(f"{field} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ReceivablesInputError(f"{field} must be canonical UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReceivablesInputError(f"{field} must include UTC authority")
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="auto").replace(
        "+00:00", "Z"
    )


def _identity(raw: Any, field: str) -> Tuple[str, int]:
    if type(raw) is not dict:
        raise ReceivablesInputError(f"{field} must be an object")
    repo = raw.get("repo")
    pr = raw.get("pr")
    if type(repo) is not str or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo
    ):
        raise ReceivablesInputError(f"{field}.repo must be owner/name")
    if type(pr) is not int or pr <= 0 or pr > 2_147_483_647:
        raise ReceivablesInputError(f"{field}.pr must be a positive integer")
    return repo, pr


def _identity_key(repo: str, pr: int) -> Tuple[str, int]:
    return repo.casefold(), pr


def _normalize_policy(policy: Any) -> Tuple[Dict[str, int], str]:
    raw = _strict_keys(policy, _POLICY_KEYS, "policy")
    if raw["schema"] != _POLICY_SCHEMA:
        raise ReceivablesInputError(
            "policy schema is invalid; legacy receivables policy is intentionally unsupported"
        )
    _positive_int(raw["version"], "policy.version", 2_147_483_647)
    review = _positive_int(
        raw["review_after_hours"],
        "policy.review_after_hours",
        _MAX_THRESHOLD_HOURS,
    )
    aged = _positive_int(
        raw["aged_review_after_hours"],
        "policy.aged_review_after_hours",
        _MAX_THRESHOLD_HOURS,
    )
    if aged <= review:
        raise ReceivablesInputError(
            "aged_review_after_hours must exceed review_after_hours"
        )
    normalized = {
        "schema": _POLICY_SCHEMA,
        "version": raw["version"],
        "review_after_hours": review,
        "aged_review_after_hours": aged,
    }
    return normalized, _sha256(normalized)


def _payment_hashes(
    value: Any, *, field: str, globally_used: set[str]
) -> List[str]:
    if type(value) is not list or len(value) > 1_000:
        raise ReceivablesInputError(f"{field} must be a bounded list")
    result: List[str] = []
    local = set()
    for index, item in enumerate(value):
        if type(item) is not dict:
            raise ReceivablesInputError(f"{field}[{index}] must be an object")
        digest = item.get("history_sha256")
        if type(digest) is not str or not _SHA256_RE.fullmatch(digest):
            raise ReceivablesInputError(
                f"{field}[{index}].history_sha256 must be lowercase SHA-256"
            )
        if digest in local or digest in globally_used:
            raise ReceivablesInputError(
                "payment evidence cannot be duplicated or cross-used"
            )
        local.add(digest)
        globally_used.add(digest)
        result.append(digest)
    return result


def _joined_scope(
    closeout_rows: Any,
    settlement_rows: Any,
    *,
    now: datetime,
    policy: Dict[str, int],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    if (
        type(closeout_rows) is not list
        or not closeout_rows
        or len(closeout_rows) > _MAX_ITEMS
    ):
        raise ReceivablesInputError("live closeout must be a non-empty bounded list")
    if (
        type(settlement_rows) is not list
        or len(settlement_rows) != len(closeout_rows)
    ):
        raise ReceivablesInputError(
            "settlement scope must exactly match live closeout scope"
        )

    closeout: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for index, raw in enumerate(closeout_rows):
        repo, pr = _identity(raw, f"closeout[{index}]")
        key = _identity_key(repo, pr)
        if key in closeout:
            raise ReceivablesInputError(f"duplicate closeout identity: {repo}#{pr}")
        state = raw.get("state")
        if type(state) is not str or state not in _ALLOWED_CLOSEOUT_STATES:
            raise ReceivablesInputError(f"{repo}#{pr} has invalid closeout state")
        currency = raw.get("currency")
        if type(currency) is not str or not re.fullmatch(
            r"[A-Z][A-Z0-9_.-]{1,11}", currency
        ):
            raise ReceivablesInputError(f"{repo}#{pr} has invalid currency")
        advertised = _decimal(
            raw.get("advertised_amount"), f"{repo}#{pr}.advertised_amount"
        )
        if advertised <= 0:
            raise ReceivablesInputError(
                f"{repo}#{pr}.advertised_amount must be positive"
            )
        merged_at_raw = raw.get("merged_at")
        if state == "MERGED":
            merged_at = _utc(merged_at_raw, f"{repo}#{pr}.merged_at")
            if merged_at > now:
                raise ReceivablesInputError(
                    f"{repo}#{pr}.merged_at cannot be in the future"
                )
        else:
            if merged_at_raw is not None:
                raise ReceivablesInputError(
                    f"{repo}#{pr} non-merged state cannot carry merged_at"
                )
            merged_at = None
        route = raw.get("settlement_followup_url")
        if route is not None and (
            type(route) is not str
            or not re.fullmatch(r"https?://[^\s]+", route)
        ):
            raise ReceivablesInputError(
                f"{repo}#{pr}.settlement_followup_url is invalid"
            )
        canonical_url = raw.get("canonical_url")
        if (
            type(canonical_url) is not str
            or canonical_url != f"https://github.com/{repo}/pull/{pr}"
        ):
            raise ReceivablesInputError(f"{repo}#{pr} canonical URL mismatch")
        head_sha = raw.get("head_sha")
        if type(head_sha) is not str or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
            raise ReceivablesInputError(f"{repo}#{pr} has invalid head SHA")
        closeout[key] = {
            "repo": repo,
            "pr": pr,
            "state": state,
            "currency": currency,
            "advertised": advertised,
            "merged_at": merged_at,
            "settlement_route": route,
            "canonical_url": canonical_url,
            "head_sha": head_sha,
        }

    settlement_map: Dict[Tuple[str, int], Dict[str, Any]] = {}
    used_evidence: set[str] = set()
    for index, raw in enumerate(settlement_rows):
        repo, pr = _identity(raw, f"settlement[{index}]")
        key = _identity_key(repo, pr)
        if key in settlement_map:
            raise ReceivablesInputError(
                f"duplicate settlement identity: {repo}#{pr}"
            )
        if key not in closeout:
            raise ReceivablesInputError(
                f"settlement contains extra identity: {repo}#{pr}"
            )
        source = closeout[key]
        if raw.get("state") != source["state"]:
            raise ReceivablesInputError(
                f"{repo}#{pr} settlement state disagrees with live closeout"
            )
        if raw.get("currency") != source["currency"]:
            raise ReceivablesInputError(
                f"{repo}#{pr} settlement currency disagrees with live closeout"
            )
        advertised = _decimal(
            raw.get("advertised_amount"),
            f"{repo}#{pr}.settlement advertised_amount",
        )
        if advertised != source["advertised"]:
            raise ReceivablesInputError(
                f"{repo}#{pr} settlement advertised amount disagrees with live closeout"
            )
        verified = _decimal(
            raw.get("verified_amount"), f"{repo}#{pr}.verified_amount"
        )
        if verified > advertised:
            raise ReceivablesInputError(
                f"{repo}#{pr} verified amount exceeds advertised amount"
            )
        status = raw.get("cash_status")
        if type(status) is not str or status not in _ALLOWED_CASH_STATUS:
            raise ReceivablesInputError(f"{repo}#{pr} has invalid cash_status")
        evidence = _payment_hashes(
            raw.get("payment_evidence"),
            field=f"{repo}#{pr}.payment_evidence",
            globally_used=used_evidence,
        )
        if status == "verified_paid":
            if source["state"] != "MERGED" or verified != advertised or not evidence:
                raise ReceivablesInputError(
                    f"{repo}#{pr} verified_paid is inconsistent"
                )
        elif status == "partially_verified":
            if (
                source["state"] != "MERGED"
                or verified <= 0
                or verified >= advertised
                or not evidence
            ):
                raise ReceivablesInputError(
                    f"{repo}#{pr} partially_verified is inconsistent"
                )
        elif verified != 0 or evidence:
            raise ReceivablesInputError(
                f"{repo}#{pr} not_inferred must have zero cash/evidence"
            )
        settlement_map[key] = {
            "verified": verified,
            "cash_status": status,
            "payment_evidence_sha256s": evidence,
        }

    if set(closeout) != set(settlement_map):
        raise ReceivablesInputError(
            "settlement scope must exactly match live closeout identities"
        )

    scope_rows: List[Dict[str, Any]] = []
    merged_review: List[Dict[str, Any]] = []
    review_seconds = policy["review_after_hours"] * 3600
    aged_seconds = policy["aged_review_after_hours"] * 3600

    for key in sorted(closeout):
        source = closeout[key]
        cash = settlement_map[key]
        scope_rows.append(
            {
                "repo": source["repo"],
                "pr": source["pr"],
                "state": source["state"],
                "head_sha": source["head_sha"],
                "currency": source["currency"],
                "advertised_amount": _amount_text(source["advertised"]),
                "merged_at": (
                    None
                    if source["merged_at"] is None
                    else _iso_utc(source["merged_at"])
                ),
                "cash_status": cash["cash_status"],
                "verified_amount": _amount_text(cash["verified"]),
                "payment_evidence_sha256s": cash["payment_evidence_sha256s"],
                "settlement_route": source["settlement_route"],
            }
        )
        if source["state"] != "MERGED":
            continue
        if source["currency"] != "RTC":
            raise ReceivablesInputError(
                f"{source['repo']}#{source['pr']} merged review item must be denominated in RTC"
            )
        merged_at = source["merged_at"]
        if not isinstance(merged_at, datetime):
            raise ReceivablesInputError(
                f"{source['repo']}#{source['pr']} merged item lacks merge time"
            )
        age_seconds = int((now - merged_at).total_seconds())
        if age_seconds < 0:
            raise ReceivablesInputError("trusted merge age cannot be negative")
        unverified_advertised = source["advertised"] - cash["verified"]
        if cash["cash_status"] == "verified_paid":
            action = "SETTLED_VERIFIED"
            reason = "verified_wallet_evidence_matches_full_advertised_rtc"
        elif age_seconds >= aged_seconds:
            action = "OWNER_AGED_UNSETTLED_REVIEW"
            reason = "merged_work_unsettled_beyond_aged_review_threshold"
        elif age_seconds >= review_seconds:
            action = "OWNER_UNSETTLED_REVIEW"
            reason = "merged_work_unsettled_beyond_review_threshold"
        else:
            action = "MONITOR_UNSETTLED"
            reason = "merged_work_unsettled_below_review_threshold"

        merged_review.append(
            {
                "repo": source["repo"],
                "pr": source["pr"],
                "canonical_url": source["canonical_url"],
                "head_sha": source["head_sha"],
                "merged_at": _iso_utc(merged_at),
                "age_seconds": age_seconds,
                "age_hours_floor": age_seconds // 3600,
                "currency": "RTC",
                "advertised_rtc": _amount_text(source["advertised"]),
                "verified_rtc": _amount_text(cash["verified"]),
                "unverified_advertised_rtc": _amount_text(
                    unverified_advertised
                ),
                "cash_status": cash["cash_status"],
                "payment_evidence_sha256s": cash[
                    "payment_evidence_sha256s"
                ],
                "settlement_route": source["settlement_route"],
                "settlement_route_available": source["settlement_route"]
                is not None,
                "route_proves_prior_contact": False,
                "payable_obligation_proven": False,
                "unverified_advertised_amount_is_debt": False,
                "action": action,
                "reason": reason,
            }
        )

    merged_review.sort(
        key=lambda row: (
            _ACTION_PRIORITY[row["action"]],
            -row["age_seconds"],
            row["repo"].casefold(),
            row["pr"],
        )
    )
    return scope_rows, merged_review, _sha256(scope_rows)


def compile_receivables_review(
    manifest_items: Any,
    bindings: Any,
    policy: Any,
    *,
    wallet: Any,
    max_pages: Any = 10,
) -> Dict[str, Any]:
    """Re-run live GitHub + wallet authority and compile an owner review queue."""
    if (
        type(manifest_items) is not list
        or not manifest_items
        or len(manifest_items) > _MAX_ITEMS
    ):
        raise ReceivablesInputError(
            "manifest items must be a non-empty bounded list"
        )
    if type(bindings) is not list or len(bindings) > _MAX_ITEMS:
        raise ReceivablesInputError("bindings must be a bounded list")
    if (
        type(wallet) is not str
        or not wallet
        or wallet != wallet.strip()
        or any(char.isspace() or not char.isprintable() for char in wallet)
        or len(wallet) > 256
    ):
        raise ReceivablesInputError(
            "wallet must be one bounded printable identifier"
        )
    pages = _positive_int(max_pages, "max_pages", 100)
    normalized_policy, policy_digest = _normalize_policy(policy)

    live_closeout = revenue_closeout.build_closeout_queue(
        manifest_items, max_pages=pages
    )
    history, history_wallet = revenue_settlement._query_canonical_history(wallet)
    reconciled = revenue_settlement.reconcile_cash(
        live_closeout,
        history,
        bindings,
        wallet=wallet,
        history_wallet=history_wallet,
        history_source="queried_wallet",
    )
    # Sample trusted time after network-backed reads. No caller-selected time is
    # accepted by the live authority-producing API.
    now = _trusted_utc_now().astimezone(timezone.utc)
    scope_rows, merged_review, source_scope_digest = _joined_scope(
        live_closeout,
        reconciled,
        now=now,
        policy=normalized_policy,
    )

    advertised = sum(
        (Decimal(row["advertised_rtc"]) for row in merged_review), Decimal("0")
    )
    verified = sum(
        (Decimal(row["verified_rtc"]) for row in merged_review), Decimal("0")
    )
    unverified = sum(
        (
            Decimal(row["unverified_advertised_rtc"])
            for row in merged_review
        ),
        Decimal("0"),
    )
    summary = {
        "scanned_item_count": len(scope_rows),
        "merged_item_count": len(merged_review),
        "settled_verified_items": sum(
            row["action"] == "SETTLED_VERIFIED" for row in merged_review
        ),
        "partially_verified_items": sum(
            row["cash_status"] == "partially_verified"
            for row in merged_review
        ),
        "zero_verified_cash_items": sum(
            row["cash_status"] == "not_inferred" for row in merged_review
        ),
        "owner_unsettled_review_items": sum(
            row["action"] == "OWNER_UNSETTLED_REVIEW"
            for row in merged_review
        ),
        "owner_aged_unsettled_review_items": sum(
            row["action"] == "OWNER_AGED_UNSETTLED_REVIEW"
            for row in merged_review
        ),
        "monitor_items": sum(
            row["action"] == "MONITOR_UNSETTLED" for row in merged_review
        ),
        "advertised_rtc_total": _amount_text(advertised),
        "verified_rtc_total": _amount_text(verified),
        "unverified_advertised_rtc_total": _amount_text(unverified),
        "cash_basis": "live_canonical_wallet_reconciliation",
        "merge_basis": "live_github_closeout",
        "unverified_advertised_amount_is_receivable": False,
        "payable_obligation_claim": False,
        "debt_claim": False,
        "prior_contact_claim": False,
        "advertised_reward_used_as_cash": False,
        "merge_used_as_cash": False,
        "accounting_revenue_claim": False,
        "tax_claim": False,
        "autonomous_contact_authorized": False,
        "claim_or_submission_authorized": False,
        "wallet_or_provider_mutation_authorized": False,
        "spend_authorized": False,
    }
    payload: Dict[str, Any] = {
        "schema": _RECEIPT_SCHEMA,
        "trusted_as_of": _iso_utc(now),
        "wallet": wallet,
        "history_source": "queried_wallet",
        "source_scope_sha256": source_scope_digest,
        "policy_sha256": policy_digest,
        "policy": normalized_policy,
        "summary": summary,
        "merged_work_review": merged_review,
    }
    payload["receipt_sha256"] = _sha256(payload)
    return payload


def verify_receipt_integrity(receipt: Any) -> bool:
    """Check receipt self-integrity only; rerun compile for current authority."""
    if type(receipt) is not dict:
        return False
    digest = receipt.get("receipt_sha256")
    if type(digest) is not str or not _SHA256_RE.fullmatch(digest):
        return False
    candidate = dict(receipt)
    candidate.pop("receipt_sha256", None)
    try:
        if candidate.get("schema") != _RECEIPT_SCHEMA:
            return False
        summary = candidate.get("summary")
        if type(summary) is not dict:
            return False
        required_false = (
            "unverified_advertised_amount_is_receivable",
            "payable_obligation_claim",
            "debt_claim",
            "prior_contact_claim",
            "advertised_reward_used_as_cash",
            "merge_used_as_cash",
            "accounting_revenue_claim",
            "tax_claim",
            "autonomous_contact_authorized",
            "claim_or_submission_authorized",
            "wallet_or_provider_mutation_authorized",
            "spend_authorized",
        )
        if any(summary.get(key) is not False for key in required_false):
            return False
        rows = candidate.get("merged_work_review")
        if type(rows) is not list:
            return False
        for row in rows:
            if type(row) is not dict:
                return False
            if (
                row.get("route_proves_prior_contact") is not False
                or row.get("payable_obligation_proven") is not False
                or row.get("unverified_advertised_amount_is_debt") is not False
            ):
                return False
        return _sha256(candidate) == digest
    except ReceivablesInputError:
        return False


def _strict_json(path: str) -> Any:
    source = Path(path)
    try:
        if not source.is_file() or source.stat().st_size > _MAX_JSON_BYTES:
            raise ReceivablesInputError(
                f"{path} must be a bounded ordinary JSON file"
            )

        def unique_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
            result: Dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ReceivablesInputError(
                        f"{path} contains duplicate JSON key {key!r}"
                    )
                result[key] = value
            return result

        return json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ReceivablesInputError(
                    f"{path} contains non-finite JSON value {value}"
                )
            ),
            parse_int=lambda text: (
                int(text)
                if len(text.lstrip("-")) <= 96
                else (_ for _ in ()).throw(
                    ReceivablesInputError(
                        f"{path} contains an oversized JSON integer"
                    )
                )
            ),
        )
    except ReceivablesInputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReceivablesInputError(
            f"{path} is not valid bounded UTF-8 JSON"
        ) from exc


def _manifest_items(payload: Any) -> List[Dict[str, Any]]:
    raw = _strict_keys(payload, {"schema_version", "items"}, "manifest")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ReceivablesInputError(
            "manifest schema_version must be integer 1"
        )
    if type(raw["items"]) is not list:
        raise ReceivablesInputError("manifest.items must be a list")
    return raw["items"]


def _write_output(path: Optional[str], payload: Dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path is None:
        print(text, end="")
        return
    destination = Path(path)
    try:
        with destination.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise ReceivablesInputError(
            f"refusing to overwrite existing output: {path}"
        ) from exc
    except OSError as exc:
        raise ReceivablesInputError(
            f"cannot create output {path}: {exc}"
        ) from exc


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.receivables_aging",
        description=(
            "Compile a live owner review of merged RTC work awaiting verified settlement."
        ),
    )
    parser.add_argument("manifest")
    parser.add_argument("bindings")
    parser.add_argument("policy")
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        manifest = _strict_json(args.manifest)
        binding_payload = _strict_json(args.bindings)
        policy = _strict_json(args.policy)
        binding_items = revenue_settlement._schema_items(
            binding_payload, name="bindings"
        )
        receipt = compile_receivables_review(
            _manifest_items(manifest),
            binding_items,
            policy,
            wallet=args.wallet,
            max_pages=args.max_pages,
        )
        _write_output(args.output, receipt)
        return 0
    except (
        OSError,
        revenue_closeout.RevenueCloseoutError,
        revenue_closeout.RevenueCloseoutInputError,
        revenue_settlement.PayoutLookupError,
        revenue_settlement.RevenueSettlementInputError,
        revenue_settlement.RevenueSettlementEvidenceError,
        ReceivablesInputError,
    ) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
