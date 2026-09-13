# SPDX-License-Identifier: MIT
"""Evidence-bound payout discrepancy review packets.

This module compares separately evidenced sponsor acceptance/award terms against
an observed settlement.  It can quantify an exact same-currency difference and
compile an owner-review reconciliation draft, but it never sends a message,
mutates a payment rail, authenticates evidence by URL/digest alone, performs FX
conversion, or promotes an advertised reward into debt.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Optional
from urllib.parse import urlsplit

_SCHEMA = "bounty-payout-dispute/v1"
_INPUT_SCHEMA = "bounty-payout-dispute-input/v1"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9_.-]{1,11}$")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ROUTE_TYPES = frozenset({"HOSTED_HANDLE", "PAYMENT_LINK", "WALLET", "OTHER"})
_ACCEPTANCE_KINDS = frozenset({"SPONSOR_ACCEPTED", "AWARDED"})
_MAX_JSON_BYTES = 1024 * 1024
_MAX_TEXT = 4096


class PayoutDisputeInputError(ValueError):
    """Malformed, ambiguous, or authority-expanding input."""


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
        raise PayoutDisputeInputError("value is not canonical JSON") from exc
    if len(payload) > _MAX_JSON_BYTES:
        raise PayoutDisputeInputError("canonical JSON is too large")
    return payload


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _strict_object(value: Any, *, keys: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise PayoutDisputeInputError(
            f"{field} must contain exactly {sorted(keys)}"
        )
    return value


def _text(value: Any, *, field: str, max_len: int = 512) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > max_len
    ):
        raise PayoutDisputeInputError(
            f"{field} must be non-empty bounded text without edge whitespace"
        )
    if any(ord(ch) < 32 and ch not in "\t" for ch in value):
        raise PayoutDisputeInputError(f"{field} contains control characters")
    return value


def _url(value: Any, *, field: str) -> str:
    text = _text(value, field=field, max_len=2048)
    if any(ch.isspace() for ch in text):
        raise PayoutDisputeInputError(f"{field} must not contain whitespace")
    parsed = urlsplit(text)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise PayoutDisputeInputError(
            f"{field} must be an absolute credential-free HTTPS URL without fragment"
        )
    return text


def _currency(value: Any, *, field: str) -> str:
    text = _text(value, field=field, max_len=12)
    if not _CURRENCY_RE.fullmatch(text):
        raise PayoutDisputeInputError(
            f"{field} must be an uppercase currency/token code"
        )
    return text


def _amount(value: Any, *, field: str, positive: bool = True) -> tuple[Decimal, str]:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise PayoutDisputeInputError(f"{field} must be a bounded decimal")
    raw = str(value)
    if len(raw) > 64:
        raise PayoutDisputeInputError(f"{field} is too large")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise PayoutDisputeInputError(f"{field} must be a bounded decimal") from exc
    exp = amount.as_tuple().exponent
    if (
        not amount.is_finite()
        or (amount <= 0 if positive else amount < 0)
        or len(amount.as_tuple().digits) > 30
        or not isinstance(exp, int)
        or abs(exp) > 18
    ):
        qualifier = "positive " if positive else "non-negative "
        raise PayoutDisputeInputError(
            f"{field} must be a bounded {qualifier}decimal"
        )
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text == "-0":
        text = "0"
    return amount, text


def _timestamp(value: Any, *, field: str) -> str:
    text = _text(value, field=field, max_len=40)
    if not text.endswith("Z"):
        raise PayoutDisputeInputError(f"{field} must be UTC RFC3339 ending in Z")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise PayoutDisputeInputError(f"{field} must be valid RFC3339") from exc
    if parsed.tzinfo != timezone.utc:
        raise PayoutDisputeInputError(f"{field} must be UTC")
    canonical = parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
    if text != canonical:
        raise PayoutDisputeInputError(
            f"{field} must use canonical whole-second UTC RFC3339"
        )
    return canonical


def _validate_evidence(
    value: Any,
    *,
    field: str,
    extra_keys: set[str],
) -> dict[str, Any]:
    keys = {"evidence_ref", "evidence_sha256"} | extra_keys
    obj = _strict_object(value, keys=keys, field=field)
    evidence_ref = _url(obj["evidence_ref"], field=f"{field}.evidence_ref")
    evidence_sha256 = _text(
        obj["evidence_sha256"], field=f"{field}.evidence_sha256", max_len=64
    )
    if not _SHA256_RE.fullmatch(evidence_sha256):
        raise PayoutDisputeInputError(
            f"{field}.evidence_sha256 must be lowercase 64-hex"
        )
    out = dict(obj)
    out["evidence_ref"] = evidence_ref
    out["evidence_sha256"] = evidence_sha256
    return out


def _validate_input(payload: Any) -> dict[str, Any]:
    root = _strict_object(
        payload,
        keys={
            "schema",
            "sponsor_name",
            "work",
            "acceptance",
            "settlement",
            "payout_route",
        },
        field="input",
    )
    if root["schema"] != _INPUT_SCHEMA:
        raise PayoutDisputeInputError(f"input.schema must be {_INPUT_SCHEMA}")
    sponsor = _text(root["sponsor_name"], field="sponsor_name", max_len=160)

    work = _strict_object(
        root["work"],
        keys={
            "repo",
            "pr",
            "canonical_url",
            "head_sha",
            "state",
            "advertised_amount",
            "currency",
        },
        field="work",
    )
    repo = _text(work["repo"], field="work.repo", max_len=200)
    if not _REPO_RE.fullmatch(repo) or any(
        part in {".", ".."} for part in repo.split("/")
    ):
        raise PayoutDisputeInputError("work.repo must be owner/name")
    pr = work["pr"]
    if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
        raise PayoutDisputeInputError("work.pr must be a positive integer")
    canonical_url = _url(work["canonical_url"], field="work.canonical_url")
    if canonical_url != f"https://github.com/{repo}/pull/{pr}":
        raise PayoutDisputeInputError("work.canonical_url must match repo/pr exactly")
    head_sha = _text(work["head_sha"], field="work.head_sha", max_len=40)
    if not _SHA40_RE.fullmatch(head_sha):
        raise PayoutDisputeInputError("work.head_sha must be lowercase 40-hex")
    if work["state"] != "MERGED":
        raise PayoutDisputeInputError("work.state must be MERGED")
    _, advertised_text = _amount(
        work["advertised_amount"], field="work.advertised_amount"
    )
    advertised_currency = _currency(work["currency"], field="work.currency")

    acceptance = _validate_evidence(
        root["acceptance"],
        field="acceptance",
        extra_keys={"kind", "accepted_amount", "currency"},
    )
    kind = _text(acceptance["kind"], field="acceptance.kind", max_len=32)
    if kind not in _ACCEPTANCE_KINDS:
        raise PayoutDisputeInputError(
            f"acceptance.kind must be one of {sorted(_ACCEPTANCE_KINDS)}"
        )
    _, accepted_text = _amount(
        acceptance["accepted_amount"], field="acceptance.accepted_amount"
    )
    accepted_currency = _currency(
        acceptance["currency"], field="acceptance.currency"
    )

    settlement = _validate_evidence(
        root["settlement"],
        field="settlement",
        extra_keys={
            "amount_received",
            "currency",
            "transaction_id",
            "settled_at",
        },
    )
    _, received_text = _amount(
        settlement["amount_received"],
        field="settlement.amount_received",
        positive=False,
    )
    settlement_currency = _currency(
        settlement["currency"], field="settlement.currency"
    )
    transaction_id = _text(
        settlement["transaction_id"],
        field="settlement.transaction_id",
        max_len=512,
    )
    settled_at = _timestamp(
        settlement["settled_at"], field="settlement.settled_at"
    )

    route = _strict_object(
        root["payout_route"], keys={"type", "value"}, field="payout_route"
    )
    route_type = _text(route["type"], field="payout_route.type", max_len=32)
    if route_type not in _ROUTE_TYPES:
        raise PayoutDisputeInputError(
            f"payout_route.type must be one of {sorted(_ROUTE_TYPES)}"
        )
    route_value = _text(
        route["value"], field="payout_route.value", max_len=512
    )
    if route_type == "PAYMENT_LINK":
        _url(route_value, field="payout_route.value")

    return {
        "schema": _INPUT_SCHEMA,
        "sponsor_name": sponsor,
        "work": {
            "repo": repo,
            "pr": pr,
            "canonical_url": canonical_url,
            "head_sha": head_sha,
            "state": "MERGED",
            "advertised_amount": advertised_text,
            "currency": advertised_currency,
        },
        "acceptance": {
            "kind": kind,
            "accepted_amount": accepted_text,
            "currency": accepted_currency,
            "evidence_ref": acceptance["evidence_ref"],
            "evidence_sha256": acceptance["evidence_sha256"],
        },
        "settlement": {
            "amount_received": received_text,
            "currency": settlement_currency,
            "transaction_id": transaction_id,
            "settled_at": settled_at,
            "evidence_ref": settlement["evidence_ref"],
            "evidence_sha256": settlement["evidence_sha256"],
        },
        "payout_route": {
            "type": route_type,
            "value": route_value,
        },
    }


def _compose(normalized: dict[str, Any]) -> dict[str, Any]:
    work = normalized["work"]
    acceptance = normalized["acceptance"]
    settlement = normalized["settlement"]
    route = normalized["payout_route"]
    sponsor = normalized["sponsor_name"]

    accepted_currency = acceptance["currency"]
    settlement_currency = settlement["currency"]
    common = {
        "advertised_amount": work["advertised_amount"],
        "advertised_currency": work["currency"],
        "accepted_amount": acceptance["accepted_amount"],
        "accepted_currency": accepted_currency,
        "received_amount": settlement["amount_received"],
        "received_currency": settlement_currency,
        "difference_amount": None,
        "difference_currency": None,
    }

    subject: Optional[str] = None
    body: Optional[str] = None
    if settlement_currency != accepted_currency:
        disposition = "HOLD_CURRENCY_MISMATCH"
        reason = (
            "accepted and observed settlement currencies differ; "
            "no FX conversion or shortfall inference is permitted"
        )
    else:
        accepted, _ = _amount(
            acceptance["accepted_amount"], field="acceptance.accepted_amount"
        )
        received, _ = _amount(
            settlement["amount_received"],
            field="settlement.amount_received",
            positive=False,
        )
        difference = accepted - received
        diff_text = format(abs(difference), "f")
        if "." in diff_text:
            diff_text = diff_text.rstrip("0").rstrip(".")
        common["difference_amount"] = diff_text
        common["difference_currency"] = accepted_currency
        if difference > 0:
            disposition = "READY_FOR_OWNER_UNDERPAYMENT_REVIEW"
            reason = (
                "observed settlement is lower than separately evidenced accepted terms"
            )
            ref = f"{work['repo']}#{work['pr']}"
            subject = (
                f"{ref} — payout reconciliation for observed "
                f"{diff_text} {accepted_currency} difference"
            )
            body = (
                f"Hi {sponsor},\n\n"
                "I’m requesting reconciliation of the accepted payout and the "
                "settlement I observed for the contribution below.\n\n"
                f"Merged work: {work['canonical_url']}\n"
                f"Exact submitted head: {work['head_sha']}\n"
                f"Acceptance evidence: {acceptance['evidence_ref']}\n"
                f"Accepted amount: {acceptance['accepted_amount']} {accepted_currency}\n"
                f"Observed settlement: {settlement['amount_received']} {accepted_currency}\n"
                f"Settlement evidence: {settlement['evidence_ref']}\n"
                f"Settlement transaction/reference: {settlement['transaction_id']}\n"
                f"Observed difference: {diff_text} {accepted_currency}\n\n"
                "Please confirm whether the difference is pending, fee-related, "
                "or requires another action from me. If an additional payout is "
                "due under your records, please use the existing payout route "
                f"({route['type']} — {route['value']}) and confirm when initiated.\n\n"
                "Thanks,\nBryce"
            )
        elif difference == 0:
            disposition = "NO_SHORTFALL"
            reason = "observed settlement equals separately evidenced accepted terms"
        else:
            disposition = "OVERPAYMENT_REQUIRES_OWNER_REVIEW"
            reason = (
                "observed settlement exceeds separately evidenced accepted terms; "
                "no retention, refund, or mutation authority is inferred"
            )

    if subject is not None and len(subject) > 998:
        raise PayoutDisputeInputError("rendered subject is too large")
    if body is not None and len(body) > _MAX_TEXT:
        raise PayoutDisputeInputError("rendered reconciliation draft is too large")
    return {
        "disposition": disposition,
        "reason": reason,
        "amounts": common,
        "subject": subject,
        "body": body,
    }


def compile_payout_dispute(payload: Any) -> dict[str, Any]:
    """Compile a deterministic discrepancy review packet.

    Evidence refs and digests are caller-provided bindings.  Their authenticity,
    sponsor ownership, and legal effect are outside this compiler's authority.
    """
    normalized = _validate_input(payload)
    composed = _compose(normalized)
    core = {
        "schema": _SCHEMA,
        **composed,
        "source": normalized,
        "authority": {
            "external_send": False,
            "provider_mutation": False,
            "payment_mutation": False,
            "wallet_mutation": False,
            "refund_or_return_authority": False,
            "legal_debt_claim": False,
            "advertised_reward_is_debt": False,
            "advertised_reward_is_earned_revenue": False,
            "merge_proves_acceptance": False,
            "evidence_ref_or_digest_authenticates_source": False,
            "fx_conversion_permitted": False,
            "owner_review_required_before_contact": (
                composed["disposition"] != "NO_SHORTFALL"
            ),
        },
    }
    packet = dict(core)
    packet["receipt_sha256"] = _sha256(core)
    return packet


def verify_payout_dispute(payload: Any, packet: Any) -> bool:
    """Verify exact derivation including authority and rendered review text."""
    if not isinstance(packet, dict):
        return False
    try:
        expected = compile_payout_dispute(payload)
    except PayoutDisputeInputError:
        return False
    return packet == expected


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise PayoutDisputeInputError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def load_json(path: Path) -> Any:
    raw = path.read_bytes()
    if len(raw) > _MAX_JSON_BYTES:
        raise PayoutDisputeInputError("input file is too large")
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                PayoutDisputeInputError(f"non-finite JSON constant: {value}")
            ),
        )
    except UnicodeDecodeError as exc:
        raise PayoutDisputeInputError("input file must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise PayoutDisputeInputError("input file must contain valid JSON") from exc


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m concierge.payout_dispute")
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--verify",
        type=Path,
        help="verify an existing packet instead of compiling",
    )
    args = parser.parse_args(argv)
    try:
        payload = load_json(args.input)
        if args.verify is not None:
            packet = load_json(args.verify)
            ok = verify_payout_dispute(payload, packet)
            print(json.dumps({"verified": ok}, sort_keys=True))
            return 0 if ok else 3
        print(
            json.dumps(
                compile_payout_dispute(payload),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except (PayoutDisputeInputError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
