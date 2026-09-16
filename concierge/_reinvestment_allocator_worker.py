# SPDX-License-Identifier: MIT
"""Executable-only isolated worker for authenticated reinvestment review."""
from __future__ import annotations

if __name__ != "__main__":
    raise ImportError("reinvestment authority worker is executable-only")

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, Iterable, List, Mapping, Tuple


_PROTOCOL = "realized-reinvestment-isolated-worker/v1"
_RECEIPT_SCHEMA = "realized-reinvestment-review/v4"
_AUTHORITY_SCHEMA_VERSION = 2
_AUTHORITY_PURPOSE = "realized-reinvestment-commercial-evidence-authority/v2"
_AUTHORITY_ALGORITHM = "rsa-pkcs1v15-sha256"
_AUTHORITY_DOMAIN = b"realized-reinvestment-commercial-evidence-authority/v2\x00"
_MAX_AUTHORITY_AGE_SECONDS = 300
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_CORE_BYTES = 2 * 1024 * 1024
_CORE_GIT_BLOB_SHA1 = "5731fd0652bc94b20d1f3b2de48628f633f550a9"
_MODULUS_ENV = "REALIZED_REINVESTMENT_AUTHORITY_RSA_MODULUS_HEX"
_KEY_ID_ENV = "REALIZED_REINVESTMENT_AUTHORITY_RSA_KEY_ID"
_PROVIDER_ENV = "REALIZED_REINVESTMENT_AUTHORIZED_PROVIDER"
_PRINCIPAL_ENV = "REALIZED_REINVESTMENT_AUTHORIZED_PRINCIPAL_SHA256"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX_RE = re.compile(r"^[0-9a-f]+$")
_AUTHORITY_KEYS = frozenset(
    {
        "schema_version",
        "purpose",
        "algorithm",
        "key_id",
        "public_key_sha256",
        "provider",
        "principal_sha256",
        "captured_at",
        "scope_sha256",
        "signature_hex",
    }
)
_REQUEST_COMMON_KEYS = frozenset(
    {
        "protocol",
        "operation",
        "closeout_manifest_items",
        "payment_bindings",
        "effort_log",
        "evidence_authority",
        "taxonomy",
        "policy",
        "capacity_minutes",
        "wallet",
        "max_closeout_pages",
    }
)
_SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


class WorkerInputError(ValueError):
    pass


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
        raise WorkerInputError("value is not canonical JSON") from exc
    if len(payload) > _MAX_JSON_BYTES:
        raise WorkerInputError("canonical JSON value is too large")
    return payload


def _duplicate_safe_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkerInputError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _parse_json(raw: bytes, label: str) -> Any:
    if len(raw) > _MAX_JSON_BYTES:
        raise WorkerInputError(f"{label} is too large")
    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_duplicate_safe_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                WorkerInputError(f"non-finite JSON value {value} is not allowed")
            ),
        )
    except WorkerInputError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerInputError(f"{label} is not strict UTF-8 JSON") from exc


def _snapshot_json(value: Any, label: str) -> Any:
    return _parse_json(_canonical_json(value), label)


def _strict_object(value: Any, keys: Iterable[str], label: str) -> Dict[str, Any]:
    if type(value) is not dict:
        raise WorkerInputError(f"{label} must be an object")
    expected = set(keys)
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise WorkerInputError(
            f"{label} must have exact keys (missing={missing} extra={extra})"
        )
    return dict(value)


def _text_id(value: Any, label: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise WorkerInputError(f"{label} must be a bounded identifier")
    return value


def _hex64(value: Any, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise WorkerInputError(f"{label} must be lowercase sha256 hex")
    return value


def _validate_wallet(value: Any) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > 256
        or any(char.isspace() or not char.isprintable() for char in value)
    ):
        raise WorkerInputError("wallet must be one bounded printable identifier")
    return value


def _parse_utc(value: Any, label: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise WorkerInputError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise WorkerInputError(f"{label} must be canonical UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise WorkerInputError(f"{label} must be UTC")
    if parsed.microsecond:
        raise WorkerInputError(f"{label} must not contain fractional seconds")
    canonical = parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if canonical != value:
        raise WorkerInputError(f"{label} must be canonical UTC")
    return parsed


def _load_boot_authority() -> Dict[str, Any]:
    modulus_text = os.environ.get(_MODULUS_ENV)
    key_id_text = os.environ.get(_KEY_ID_ENV)
    provider_text = os.environ.get(_PROVIDER_ENV)
    principal_text = os.environ.get(_PRINCIPAL_ENV)
    if not modulus_text or not key_id_text or not provider_text or not principal_text:
        raise WorkerInputError("commercial evidence authority boot configuration is incomplete")
    if (
        _HEX_RE.fullmatch(modulus_text) is None
        or modulus_text.startswith("0")
        or len(modulus_text) < 512
        or len(modulus_text) > 2048
        or len(modulus_text) % 2
    ):
        raise WorkerInputError("authority RSA modulus must be canonical 2048-8192 bit lowercase hex")
    modulus = int(modulus_text, 16)
    if modulus.bit_length() < 2048 or modulus.bit_length() > 8192 or modulus % 2 == 0:
        raise WorkerInputError("authority RSA modulus is outside the accepted range")
    key_id = _text_id(key_id_text, _KEY_ID_ENV)
    provider = _text_id(provider_text, _PROVIDER_ENV)
    principal = _hex64(principal_text, _PRINCIPAL_ENV)
    exponent = 65537
    fingerprint = hashlib.sha256(
        bytes.fromhex(modulus_text) + b"\x00" + str(exponent).encode("ascii")
    ).hexdigest()
    return {
        "modulus": modulus,
        "modulus_bytes": (modulus.bit_length() + 7) // 8,
        "exponent": exponent,
        "key_id": key_id,
        "provider": provider,
        "principal_sha256": principal,
        "public_key_sha256": fingerprint,
    }


def _scope_sha256(
    closeout_manifest_items: Any,
    payment_bindings: Any,
    effort_log: Any,
    *,
    wallet: str,
) -> str:
    scope = {
        "wallet": _validate_wallet(wallet),
        "closeout_manifest_items": _snapshot_json(
            closeout_manifest_items, "closeout manifest"
        ),
        "payment_bindings": _snapshot_json(payment_bindings, "payment bindings"),
        "effort_log": _snapshot_json(effort_log, "effort log"),
    }
    return hashlib.sha256(_canonical_json(scope)).hexdigest()


def _verify_rsa_signature(
    authority_core: Mapping[str, Any], signature_hex: Any, boot: Mapping[str, Any]
) -> None:
    width = boot["modulus_bytes"]
    if (
        type(signature_hex) is not str
        or len(signature_hex) != width * 2
        or _HEX_RE.fullmatch(signature_hex) is None
    ):
        raise WorkerInputError("commercial evidence authority RSA signature has invalid width or encoding")
    signature = int(signature_hex, 16)
    if signature <= 0 or signature >= boot["modulus"]:
        raise WorkerInputError("commercial evidence authority RSA signature is out of range")
    digest = hashlib.sha256(
        _AUTHORITY_DOMAIN + _canonical_json(authority_core)
    ).digest()
    digest_info = _SHA256_DIGEST_INFO_PREFIX + digest
    padding_len = width - len(digest_info) - 3
    if padding_len < 8:
        raise WorkerInputError("authority RSA modulus is too small")
    expected = b"\x00\x01" + (b"\xff" * padding_len) + b"\x00" + digest_info
    recovered = pow(signature, boot["exponent"], boot["modulus"]).to_bytes(
        width, "big"
    )
    if not hmac.compare_digest(recovered, expected):
        raise WorkerInputError("commercial evidence authority RSA signature mismatch")


def _verify_authority(request: Mapping[str, Any], boot: Mapping[str, Any]) -> Dict[str, Any]:
    authority = _strict_object(
        request["evidence_authority"], _AUTHORITY_KEYS, "evidence_authority"
    )
    if (
        type(authority["schema_version"]) is not int
        or authority["schema_version"] != _AUTHORITY_SCHEMA_VERSION
    ):
        raise WorkerInputError("evidence_authority.schema_version mismatch")
    if authority["purpose"] != _AUTHORITY_PURPOSE:
        raise WorkerInputError("evidence_authority.purpose mismatch")
    if authority["algorithm"] != _AUTHORITY_ALGORITHM:
        raise WorkerInputError("evidence_authority.algorithm mismatch")
    key_id = _text_id(authority["key_id"], "evidence_authority.key_id")
    provider = _text_id(authority["provider"], "evidence_authority.provider")
    principal = _hex64(
        authority["principal_sha256"], "evidence_authority.principal_sha256"
    )
    fingerprint = _hex64(
        authority["public_key_sha256"], "evidence_authority.public_key_sha256"
    )
    if key_id != boot["key_id"]:
        raise WorkerInputError("commercial evidence authority key_id is not boot-authorized")
    if provider != boot["provider"]:
        raise WorkerInputError("commercial evidence authority provider is not boot-authorized")
    if not hmac.compare_digest(principal, boot["principal_sha256"]):
        raise WorkerInputError("commercial evidence authority principal is not boot-authorized")
    if not hmac.compare_digest(fingerprint, boot["public_key_sha256"]):
        raise WorkerInputError("commercial evidence authority public key is not boot-authorized")
    scope = _hex64(authority["scope_sha256"], "evidence_authority.scope_sha256")
    expected_scope = _scope_sha256(
        request["closeout_manifest_items"],
        request["payment_bindings"],
        request["effort_log"],
        wallet=request["wallet"],
    )
    if not hmac.compare_digest(scope, expected_scope):
        raise WorkerInputError("commercial evidence authority scope mismatch")
    captured = _parse_utc(
        authority["captured_at"], "evidence_authority.captured_at"
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    age = (now - captured).total_seconds()
    if age < 0:
        raise WorkerInputError("commercial evidence authority capture is in the future")
    if age > _MAX_AUTHORITY_AGE_SECONDS:
        raise WorkerInputError("commercial evidence authority capture is stale")
    normalized = {
        "schema_version": _AUTHORITY_SCHEMA_VERSION,
        "purpose": _AUTHORITY_PURPOSE,
        "algorithm": _AUTHORITY_ALGORITHM,
        "key_id": key_id,
        "public_key_sha256": fingerprint,
        "provider": provider,
        "principal_sha256": principal,
        "captured_at": captured.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope_sha256": scope,
    }
    _verify_rsa_signature(normalized, authority["signature_hex"], boot)
    normalized["signature_hex"] = authority["signature_hex"]
    return normalized


def _load_core() -> Dict[str, Any]:
    source_path = Path(__file__).with_name("_reinvestment_allocator_core.source")
    try:
        source = source_path.read_bytes()
    except OSError as exc:
        raise WorkerInputError("pinned allocator core resource is unavailable") from exc
    if len(source) > _MAX_CORE_BYTES:
        raise WorkerInputError("pinned allocator core resource is too large")
    git_sha1 = hashlib.sha1(
        b"blob " + str(len(source)).encode("ascii") + b"\x00" + source
    ).hexdigest()
    if not hmac.compare_digest(git_sha1, _CORE_GIT_BLOB_SHA1):
        raise WorkerInputError("pinned allocator core resource digest mismatch")
    try:
        text = source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise WorkerInputError("pinned allocator core resource is not UTF-8") from exc
    namespace: Dict[str, Any] = {
        "__name__": "concierge._isolated_reinvestment_allocator_core",
        "__file__": str(source_path),
        "__package__": "concierge",
    }
    try:
        code = compile(text, str(source_path), "exec", dont_inherit=True, optimize=0)
        exec(code, namespace, namespace)
    except Exception as exc:
        raise WorkerInputError("pinned allocator core could not be loaded") from exc
    for name in (
        "compile_reinvestment_review",
        "verify_receipt_integrity_only",
        "ReinvestmentInputError",
    ):
        if name not in namespace:
            raise WorkerInputError("pinned allocator core surface is incomplete")
    return namespace


def _wrap_core_receipt(
    core_receipt: Any, authority: Mapping[str, Any], core: Mapping[str, Any]
) -> Dict[str, Any]:
    if type(core_receipt) is not dict:
        raise WorkerInputError("allocator core returned a non-object receipt")
    frozen = _snapshot_json(core_receipt, "allocator core receipt")
    if not core["verify_receipt_integrity_only"](deepcopy(frozen)):
        raise WorkerInputError("allocator core receipt failed self-integrity")
    source_digest = frozen.pop("receipt_sha256")
    frozen["schema"] = _RECEIPT_SCHEMA
    frozen["source_allocator_receipt_sha256"] = source_digest
    inherited = frozen.get("authority")
    if type(inherited) is not dict:
        raise WorkerInputError("allocator core authority metadata is malformed")
    frozen["authority"] = dict(inherited)
    frozen["authority"].update(
        {
            "commercial_attribution_effort_authority": "fresh_external_rsa_signature_verified_in_isolated_worker",
            "commercial_evidence_authority": deepcopy(dict(authority)),
            "commercial_evidence_authority_sha256": hashlib.sha256(
                _canonical_json(authority)
            ).hexdigest(),
            "commercial_evidence_signature_sha256": hashlib.sha256(
                bytes.fromhex(authority["signature_hex"])
            ).hexdigest(),
            "isolated_worker_protocol": _PROTOCOL,
            "isolated_provider_composition": True,
            "boot_configuration_sealed_before_request": True,
            "parent_module_dependency_rebinding_accepted": False,
            "allocator_core_git_blob_sha1": _CORE_GIT_BLOB_SHA1,
            "symmetric_signing_secret_present_in_review_process": False,
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
    frozen["receipt_sha256"] = hashlib.sha256(_canonical_json(frozen)).hexdigest()
    return frozen


def _normalize_request(value: Any) -> Dict[str, Any]:
    if type(value) is not dict:
        raise WorkerInputError("request must be an object")
    operation = value.get("operation")
    expected = set(_REQUEST_COMMON_KEYS)
    if operation == "verify-current":
        expected.add("receipt")
    if set(value) != expected:
        raise WorkerInputError("request has unexpected or missing keys")
    if value.get("protocol") != _PROTOCOL:
        raise WorkerInputError("request protocol mismatch")
    if operation not in {"compile", "verify-current"}:
        raise WorkerInputError("request operation is invalid")
    if type(value.get("capacity_minutes")) is not int or isinstance(
        value.get("capacity_minutes"), bool
    ):
        raise WorkerInputError("capacity_minutes must be an integer")
    if type(value.get("max_closeout_pages")) is not int or isinstance(
        value.get("max_closeout_pages"), bool
    ):
        raise WorkerInputError("max_closeout_pages must be an integer")
    request = _snapshot_json(value, "request")
    request["wallet"] = _validate_wallet(request["wallet"])
    return request


def _compile_request(request: Mapping[str, Any], boot: Mapping[str, Any]) -> Dict[str, Any]:
    # All caller-authored commercial facts are externally authenticated before
    # provider modules or the allocator core are even loaded.
    authority = _verify_authority(request, boot)
    core = _load_core()
    try:
        core_receipt = core["compile_reinvestment_review"](
            request["closeout_manifest_items"],
            request["payment_bindings"],
            request["effort_log"],
            request["taxonomy"],
            request["policy"],
            request["capacity_minutes"],
            wallet=request["wallet"],
            max_closeout_pages=request["max_closeout_pages"],
        )
    except Exception as exc:
        raise WorkerInputError(
            f"allocator/provider composition failed: {type(exc).__name__}"
        ) from exc
    return _wrap_core_receipt(core_receipt, authority, core)


def _write_json(stream: Any, value: Any) -> None:
    stream.buffer.write(_canonical_json(value) + b"\n")
    stream.flush()


def _main() -> int:
    # The worker runs under ``python -I``. Insert only its own repository root;
    # caller PYTHONPATH and user-site packages remain ignored.
    package_root = str(Path(__file__).resolve().parents[1])
    if package_root not in sys.path:
        sys.path.insert(0, package_root)
    raw = sys.stdin.buffer.read(_MAX_JSON_BYTES + 1)
    try:
        request = _normalize_request(_parse_json(raw, "request"))
        boot = _load_boot_authority()
        if request["operation"] == "compile":
            receipt = _compile_request(request, boot)
            _write_json(sys.stdout, {"protocol": _PROTOCOL, "receipt": receipt})
            return 0
        try:
            expected = _compile_request(request, boot)
            valid = hmac.compare_digest(
                _canonical_json(request["receipt"]), _canonical_json(expected)
            )
        except Exception:
            valid = False
        _write_json(sys.stdout, {"protocol": _PROTOCOL, "valid": valid})
        return 0
    except Exception as exc:
        message = str(exc).strip() or type(exc).__name__
        _write_json(sys.stderr, {"error": message[:1000]})
        return 2


raise SystemExit(_main())
