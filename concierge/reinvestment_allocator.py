# SPDX-License-Identifier: MIT
"""Host-authenticated realized-cash reinvestment review.

This is the public v3 adapter over the reviewed v2 allocator core.  The core
already reacquires current GitHub closeout state and canonical wallet history.
v3 closes the remaining commercial-provenance boundary: PR payment attribution
and operator effort must be covered by a fresh HMAC attestation from the
credential-owning host before either can contribute to SCALE_REVIEW_ELIGIBLE.

The attestation proves only the bound attribution/effort facts.  Taxonomy,
threshold policy, and capacity remain caller planning inputs.  No output
authorizes spend, a claim, a submission, sponsor contact, payment mutation,
accounting revenue, tax treatment, or guaranteed return.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional

from concierge import _reinvestment_allocator_core as _core

ReinvestmentInputError = _core.ReinvestmentInputError

_RECEIPT_SCHEMA = "realized-reinvestment-review/v3"
_AUTHORITY_SCHEMA_VERSION = 1
_AUTHORITY_PURPOSE = "realized-reinvestment-commercial-evidence-authority/v1"
_MAX_AUTHORITY_AGE_SECONDS = 300
_MAX_JSON_BYTES = 4 * 1024 * 1024
_KEY_ENV = "REALIZED_REINVESTMENT_HMAC_KEY_HEX"
_PROVIDER_ENV = "REALIZED_REINVESTMENT_AUTHORIZED_PROVIDER"
_PRINCIPAL_ENV = "REALIZED_REINVESTMENT_AUTHORIZED_PRINCIPAL_SHA256"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_AUTHORITY_KEYS = frozenset(
    {
        "schema_version",
        "purpose",
        "provider",
        "principal_sha256",
        "captured_at",
        "scope_sha256",
        "signature_sha256",
    }
)


def _canonical_json(value: Any) -> bytes:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RuntimeError) as exc:
        raise ReinvestmentInputError("value is not canonical JSON") from exc
    if len(payload) > _MAX_JSON_BYTES:
        raise ReinvestmentInputError("canonical JSON value is too large")
    return payload


def _snapshot_json(value: Any, label: str) -> Any:
    payload = _canonical_json(value)
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReinvestmentInputError(f"{label} could not be frozen") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _strict_object(value: Any, keys: Iterable[str], label: str) -> Dict[str, Any]:
    if type(value) is not dict:
        raise ReinvestmentInputError(f"{label} must be an object")
    expected = set(keys)
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ReinvestmentInputError(
            f"{label} must have exact keys (missing={missing} extra={extra})"
        )
    return dict(value)


def _text_id(value: Any, label: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ReinvestmentInputError(f"{label} must be a bounded identifier")
    return value


def _hex64(value: Any, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ReinvestmentInputError(f"{label} must be lowercase sha256 hex")
    return value


def _parse_utc(value: Any, label: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ReinvestmentInputError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ReinvestmentInputError(f"{label} must be canonical UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ReinvestmentInputError(f"{label} must be UTC")
    if parsed.microsecond:
        raise ReinvestmentInputError(f"{label} must not contain fractional seconds")
    canonical = parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if canonical != value:
        raise ReinvestmentInputError(f"{label} must be canonical UTC")
    return parsed


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _validate_wallet(wallet: Any) -> str:
    if (
        type(wallet) is not str
        or not wallet
        or wallet != wallet.strip()
        or len(wallet) > 256
        or any(char.isspace() or not char.isprintable() for char in wallet)
    ):
        raise ReinvestmentInputError("wallet must be one bounded printable identifier")
    return wallet


def commercial_evidence_scope_sha256(
    closeout_manifest_items: Any,
    payment_bindings: Any,
    effort_log: Any,
    *,
    wallet: str,
) -> str:
    """Digest the exact commercial attribution facts a trusted host attests."""
    wallet = _validate_wallet(wallet)
    scope = {
        "wallet": wallet,
        "closeout_manifest_items": _snapshot_json(
            closeout_manifest_items, "closeout manifest"
        ),
        "payment_bindings": _snapshot_json(payment_bindings, "payment bindings"),
        "effort_log": _snapshot_json(effort_log, "effort log"),
    }
    return _sha256(scope)


def _host_config() -> tuple[bytes, str, str]:
    key_text = os.environ.get(_KEY_ENV)
    provider_text = os.environ.get(_PROVIDER_ENV)
    principal_text = os.environ.get(_PRINCIPAL_ENV)
    if not key_text or not provider_text or not principal_text:
        raise ReinvestmentInputError(
            "commercial evidence authority host configuration is incomplete"
        )
    if len(key_text) < 64 or len(key_text) % 2:
        raise ReinvestmentInputError(
            "commercial evidence authority HMAC key must be at least 32 bytes of hex"
        )
    try:
        key = bytes.fromhex(key_text)
    except ValueError as exc:
        raise ReinvestmentInputError(
            "commercial evidence authority HMAC key must be hex"
        ) from exc
    if len(key) < 32:
        raise ReinvestmentInputError(
            "commercial evidence authority HMAC key must be at least 32 bytes"
        )
    provider = _text_id(provider_text, _PROVIDER_ENV)
    principal = _hex64(principal_text, _PRINCIPAL_ENV)
    return key, provider, principal


def _normalize_authority(value: Any) -> Dict[str, Any]:
    obj = _strict_object(value, _AUTHORITY_KEYS, "evidence_authority")
    if (
        type(obj["schema_version"]) is not int
        or obj["schema_version"] != _AUTHORITY_SCHEMA_VERSION
    ):
        raise ReinvestmentInputError("evidence_authority.schema_version mismatch")
    if obj["purpose"] != _AUTHORITY_PURPOSE:
        raise ReinvestmentInputError("evidence_authority.purpose mismatch")
    return {
        "schema_version": _AUTHORITY_SCHEMA_VERSION,
        "purpose": _AUTHORITY_PURPOSE,
        "provider": _text_id(obj["provider"], "evidence_authority.provider"),
        "principal_sha256": _hex64(
            obj["principal_sha256"], "evidence_authority.principal_sha256"
        ),
        "captured_at": _parse_utc(
            obj["captured_at"], "evidence_authority.captured_at"
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope_sha256": _hex64(
            obj["scope_sha256"], "evidence_authority.scope_sha256"
        ),
        "signature_sha256": _hex64(
            obj["signature_sha256"], "evidence_authority.signature_sha256"
        ),
    }


def _signature_core(authority: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in authority.items()
        if key != "signature_sha256"
    }


def _verify_evidence_authority(
    closeout_manifest_items: Any,
    payment_bindings: Any,
    effort_log: Any,
    authority_value: Any,
    *,
    wallet: str,
) -> Dict[str, Any]:
    authority = _normalize_authority(authority_value)
    key, provider, principal = _host_config()
    if authority["provider"] != provider:
        raise ReinvestmentInputError(
            "commercial evidence authority provider is not host-authorized"
        )
    if not hmac.compare_digest(authority["principal_sha256"], principal):
        raise ReinvestmentInputError(
            "commercial evidence authority principal is not host-authorized"
        )
    expected_scope = commercial_evidence_scope_sha256(
        closeout_manifest_items, payment_bindings, effort_log, wallet=wallet
    )
    if not hmac.compare_digest(authority["scope_sha256"], expected_scope):
        raise ReinvestmentInputError("commercial evidence authority scope mismatch")
    expected_signature = hmac.new(
        key, _canonical_json(_signature_core(authority)), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(authority["signature_sha256"], expected_signature):
        raise ReinvestmentInputError("commercial evidence authority HMAC mismatch")
    captured = _parse_utc(
        authority["captured_at"], "evidence_authority.captured_at"
    )
    now = _utc_now()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ReinvestmentInputError("verifier clock must be timezone-aware")
    now = now.astimezone(timezone.utc).replace(microsecond=0)
    age = (now - captured).total_seconds()
    if age < 0:
        raise ReinvestmentInputError(
            "commercial evidence authority capture is in the future"
        )
    if age > _MAX_AUTHORITY_AGE_SECONDS:
        raise ReinvestmentInputError("commercial evidence authority capture is stale")
    return authority


def _wrap_core_receipt(
    core_receipt: Any, authority: Mapping[str, Any]
) -> Dict[str, Any]:
    if type(core_receipt) is not dict:
        raise ReinvestmentInputError("allocator core returned a non-object receipt")
    if not _core.verify_receipt_integrity_only(deepcopy(core_receipt)):
        raise ReinvestmentInputError("allocator core receipt failed self-integrity")
    payload = deepcopy(core_receipt)
    core_receipt_sha256 = payload.pop("receipt_sha256")
    payload["schema"] = _RECEIPT_SCHEMA
    payload["source_allocator_receipt_sha256"] = core_receipt_sha256
    inherited = payload.get("authority")
    if type(inherited) is not dict:
        raise ReinvestmentInputError("allocator core authority metadata is malformed")
    payload["authority"] = dict(inherited)
    payload["authority"].update(
        {
            "commercial_attribution_effort_authority": "fresh_host_hmac_authenticated",
            "commercial_evidence_provider": authority["provider"],
            "commercial_evidence_principal_sha256": authority["principal_sha256"],
            "commercial_evidence_captured_at": authority["captured_at"],
            "commercial_evidence_scope_sha256": authority["scope_sha256"],
            "commercial_evidence_authority_sha256": _sha256(authority),
            "unsigned_payment_attribution_accepted": False,
            "unsigned_effort_evidence_accepted": False,
            "taxonomy_policy_basis": "caller_supplied_planning_inputs",
            "taxonomy_policy_authenticated_as_owner": False,
            "spend_authorized": False,
            "external_contact_authorized": False,
            "payment_or_wallet_mutation_authorized": False,
            "accounting_or_tax_claim": False,
            "future_revenue_claimed": False,
            "guaranteed_return_claimed": False,
        }
    )
    payload["receipt_sha256"] = _sha256(payload)
    return payload


def compile_reinvestment_review(
    closeout_manifest_items: Any,
    payment_bindings: Any,
    effort_log: Any,
    evidence_authority: Any,
    taxonomy: Any,
    policy: Any,
    capacity_minutes: Any,
    *,
    wallet: str,
    max_closeout_pages: int = 10,
) -> Dict[str, Any]:
    """Compile a current review only after commercial evidence is authenticated."""
    # Freeze and authenticate before any provider read in the core.
    manifest_snapshot = _snapshot_json(closeout_manifest_items, "closeout manifest")
    bindings_snapshot = _snapshot_json(payment_bindings, "payment bindings")
    effort_snapshot = _snapshot_json(effort_log, "effort log")
    authority = _verify_evidence_authority(
        manifest_snapshot,
        bindings_snapshot,
        effort_snapshot,
        evidence_authority,
        wallet=wallet,
    )
    core_receipt = _core.compile_reinvestment_review(
        manifest_snapshot,
        bindings_snapshot,
        effort_snapshot,
        taxonomy,
        policy,
        capacity_minutes,
        wallet=wallet,
        max_closeout_pages=max_closeout_pages,
    )
    return _wrap_core_receipt(core_receipt, authority)


def verify_reinvestment_receipt_current(
    receipt: Any,
    closeout_manifest_items: Any,
    payment_bindings: Any,
    effort_log: Any,
    evidence_authority: Any,
    taxonomy: Any,
    policy: Any,
    capacity_minutes: Any,
    *,
    wallet: str,
    max_closeout_pages: int = 10,
) -> bool:
    if type(receipt) is not dict:
        return False
    try:
        expected = compile_reinvestment_review(
            closeout_manifest_items,
            payment_bindings,
            effort_log,
            evidence_authority,
            taxonomy,
            policy,
            capacity_minutes,
            wallet=wallet,
            max_closeout_pages=max_closeout_pages,
        )
        return hmac.compare_digest(_canonical_json(receipt), _canonical_json(expected))
    except Exception as exc:
        # Preserve the core verifier's broad fail-closed behavior without exposing
        # provider exception text in a boolean verification API.
        if isinstance(
            exc,
            (
                OSError,
                ReinvestmentInputError,
                _core.closeout.RevenueCloseoutError,
                _core.closeout.RevenueCloseoutInputError,
                _core.settlement.PayoutLookupError,
                _core.settlement.RevenueSettlementInputError,
                _core.settlement.RevenueSettlementEvidenceError,
                _core.rue.RealizedUnitEconomicsInputError,
            ),
        ):
            return False
        raise


def verify_receipt_integrity_only(receipt: Any) -> bool:
    if type(receipt) is not dict:
        return False
    digest = receipt.get("receipt_sha256")
    if type(digest) is not str or _SHA256_RE.fullmatch(digest) is None:
        return False
    candidate = deepcopy(receipt)
    candidate.pop("receipt_sha256", None)
    try:
        return hmac.compare_digest(_sha256(candidate), digest)
    except ReinvestmentInputError:
        return False


def _load_json(path: str) -> Any:
    return _core._load_json(path)


def _schema_items(payload: Any, label: str) -> List[Dict[str, Any]]:
    return _core._schema_items(payload, label)


def _write_output(path: Optional[str], payload: Dict[str, Any]) -> None:
    _core._write_output(path, payload)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.reinvestment_allocator",
        description=(
            "Authenticate PR payment attribution + effort with fresh host HMAC, "
            "reacquire live GitHub closeout and canonical wallet cash, then prepare "
            "an advisory owner reinvestment review."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(target: argparse.ArgumentParser) -> None:
        target.add_argument("manifest", help="revenue-closeout manifest JSON")
        target.add_argument("bindings", help="payment bindings JSON")
        target.add_argument("effort", help="operator effort JSON")
        target.add_argument(
            "authority",
            help="fresh host-signed commercial evidence authority JSON",
        )
        target.add_argument("taxonomy", help="caller planning taxonomy JSON")
        target.add_argument("policy", help="caller planning policy JSON")
        target.add_argument("--wallet", required=True)
        target.add_argument("--capacity-minutes", required=True, type=int)
        target.add_argument("--max-closeout-pages", type=int, default=10)

    compile_parser = sub.add_parser("compile")
    add_common(compile_parser)
    compile_parser.add_argument("--output")

    verify_parser = sub.add_parser("verify-current")
    verify_parser.add_argument("receipt")
    add_common(verify_parser)

    args = parser.parse_args(argv)
    paths = [
        args.manifest,
        args.bindings,
        args.effort,
        args.authority,
        args.taxonomy,
        args.policy,
    ]
    if args.command == "verify-current":
        paths.append(args.receipt)
    if paths.count("-") > 1:
        parser.error("at most one input may read from stdin")
    try:
        manifest = _load_json(args.manifest)
        bindings = _load_json(args.bindings)
        effort = _load_json(args.effort)
        authority = _load_json(args.authority)
        taxonomy = _load_json(args.taxonomy)
        policy = _load_json(args.policy)
        manifest_items = _schema_items(manifest, "manifest")
        binding_items = _schema_items(bindings, "bindings")
        if args.command == "compile":
            payload = compile_reinvestment_review(
                manifest_items,
                binding_items,
                effort,
                authority,
                taxonomy,
                policy,
                args.capacity_minutes,
                wallet=args.wallet,
                max_closeout_pages=args.max_closeout_pages,
            )
            _write_output(args.output, payload)
            return 0
        receipt = _load_json(args.receipt)
        if not verify_reinvestment_receipt_current(
            receipt,
            manifest_items,
            binding_items,
            effort,
            authority,
            taxonomy,
            policy,
            args.capacity_minutes,
            wallet=args.wallet,
            max_closeout_pages=args.max_closeout_pages,
        ):
            raise ReinvestmentInputError("current receipt verification failed")
        print(receipt["receipt_sha256"])
        return 0
    except (
        OSError,
        ReinvestmentInputError,
        _core.closeout.RevenueCloseoutError,
        _core.closeout.RevenueCloseoutInputError,
        _core.settlement.PayoutLookupError,
        _core.settlement.RevenueSettlementInputError,
        _core.settlement.RevenueSettlementEvidenceError,
        _core.rue.RealizedUnitEconomicsInputError,
    ) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
