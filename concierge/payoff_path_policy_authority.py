# SPDX-License-Identifier: MIT
"""Verify-only host authority for payoff budget policy.

The v3 continuity ledger keeps policy and effort facts append-only, but its work
document is caller-controlled. A caller must not be able to turn a self-authored
higher BUDGET_POLICY generation into READY owner-review evidence.

Runtime code therefore holds *verification* authority only. A detached RSA
PKCS#1 v1.5 / SHA-256 signature binds the exact canonical policy chain, ledger
generation, prior-receipt anchor, work/opportunity scope, provider, principal and
capture time. The private signing key is deliberately absent from this module,
from process environment, and from every ordinary compile/verify API.

The trusted public key, provider and principal are captured exactly once during
package bootstrap. Per-call environment mutation can supply only the detached
capture/signature, not replace the trust anchor. Ordinary module reload preserves
the first bootstrap values. The verifier captures its dependency graph in definition
defaults, and the module rejects ordinary replacement/deletion of the verifier or
bootstrap anchor attributes.

This boundary assumes the host launches/imports the package before untrusted caller
code executes. Arbitrary mutation of private interpreter state before trusted
bootstrap (or arbitrary replacement of installed source) is equivalent to taking
over the host itself and is outside this evidence boundary. Caller documents,
post-bootstrap environment mutation, public/private module-attribute assignment,
and ordinary module reload are handled fail-closed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import types
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from . import payoff_path_gate_core as _core
from . import payoff_path_policy_v3 as _v3

AUTHORITY_PURPOSE = "bounty-payoff-owner-policy-authority/v3"
MAX_AUTHORITY_AGE_SECONDS = 300
RSA_SHA256_DIGESTINFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")

MODULUS_ENV = "BOUNTY_PAYOFF_POLICY_RSA_MODULUS_HEX"
EXPONENT_ENV = "BOUNTY_PAYOFF_POLICY_RSA_EXPONENT"
PROVIDER_ENV = "BOUNTY_PAYOFF_POLICY_AUTHORIZED_PROVIDER"
PRINCIPAL_ENV = "BOUNTY_PAYOFF_POLICY_AUTHORIZED_PRINCIPAL_SHA256"
CAPTURED_AT_ENV = "BOUNTY_PAYOFF_POLICY_AUTHORITY_CAPTURED_AT_UTC"
SIGNATURE_ENV = "BOUNTY_PAYOFF_POLICY_AUTHORITY_SIGNATURE_HEX"

# importlib.reload() executes in the existing module dictionary. Preserve the
# first trusted-bootstrap anchor instead of re-reading caller-mutable environment.
_BOOTSTRAP_MODULUS_TEXT = globals().get("_BOOTSTRAP_MODULUS_TEXT", os.environ.get(MODULUS_ENV))
_BOOTSTRAP_EXPONENT_TEXT = globals().get("_BOOTSTRAP_EXPONENT_TEXT", os.environ.get(EXPONENT_ENV))
_BOOTSTRAP_PROVIDER_TEXT = globals().get("_BOOTSTRAP_PROVIDER_TEXT", os.environ.get(PROVIDER_ENV))
_BOOTSTRAP_PRINCIPAL_TEXT = globals().get("_BOOTSTRAP_PRINCIPAL_TEXT", os.environ.get(PRINCIPAL_ENV))


def _canonical_bytes(value: Any, _dumps=json.dumps) -> bytes:
    return _dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def policy_authority_scope(
    document: Any,
    *,
    _normalize=_v3._normalize_document,
    _work_scope_sha256=_v3._work_scope_sha256,
    _deepcopy=deepcopy,
    _error=_core.PayoffPathError,
    _purpose=AUTHORITY_PURPOSE,
) -> dict[str, Any]:
    """Return the canonical owner-policy scope a trusted signer authorizes."""

    normalized = _normalize(document)
    continuity = normalized["continuity"]
    policies = [
        _deepcopy(event)
        for event in continuity["events"]
        if event["kind"] == "BUDGET_POLICY"
    ]
    if not policies:
        raise _error("current v3 document has no owner budget policy")
    return {
        "purpose": _purpose,
        "ledger_id": continuity["ledger_id"],
        "generation": continuity["generation"],
        "previous_receipt_sha256": continuity["previous_receipt_sha256"],
        "work_scope_sha256": _work_scope_sha256(normalized["work_items"]),
        "policy_events": policies,
    }


def policy_authority_scope_sha256(
    document: Any,
    *,
    _scope=policy_authority_scope,
    _digest=_core._digest,
) -> str:
    return _digest(_scope(document))


def _canonical_capture(
    value: Any,
    name: str,
    *,
    _timestamp=_core._timestamp,
    _render_timestamp=_core._render_timestamp,
    _error=_core.PayoffPathError,
) -> str:
    if type(value) is not str:
        raise _error(f"{name} must be canonical UTC text")
    parsed = _timestamp(value, name)
    rendered = _render_timestamp(parsed)
    if rendered != value:
        raise _error(f"{name} must be canonical UTC text")
    return rendered


def _signature_payload(
    document: Any,
    *,
    provider: str,
    principal_sha256: str,
    captured_at_utc: str,
    _scope_sha256=policy_authority_scope_sha256,
    _purpose=AUTHORITY_PURPOSE,
) -> dict[str, Any]:
    return {
        "purpose": _purpose,
        "provider": provider,
        "principal_sha256": principal_sha256,
        "captured_at_utc": captured_at_utc,
        "policy_scope_sha256": _scope_sha256(document),
    }


def verify_current_policy_authority(
    document: Any,
    *,
    _getenv=os.environ.get,
    _bootstrap_modulus_text=_BOOTSTRAP_MODULUS_TEXT,
    _bootstrap_exponent_text=_BOOTSTRAP_EXPONENT_TEXT,
    _bootstrap_provider_text=_BOOTSTRAP_PROVIDER_TEXT,
    _bootstrap_principal_text=_BOOTSTRAP_PRINCIPAL_TEXT,
    _opaque_ref=_core._opaque_ref,
    _sha=_core._sha,
    _timestamp=_core._timestamp,
    _error=_core.PayoffPathError,
    _canonical_capture_impl=_canonical_capture,
    _payload_impl=_signature_payload,
    _canonical_bytes_impl=_canonical_bytes,
    _sha256=hashlib.sha256,
    _compare_digest=hmac.compare_digest,
    _datetime_now=datetime.now,
    _timezone_utc=timezone.utc,
    _digestinfo_prefix=RSA_SHA256_DIGESTINFO_PREFIX,
    _max_age=MAX_AUTHORITY_AGE_SECONDS,
    _provider_env=PROVIDER_ENV,
    _principal_env=PRINCIPAL_ENV,
    _captured_env=CAPTURED_AT_ENV,
    _signature_env=SIGNATURE_ENV,
) -> dict[str, Any]:
    """Verify detached RSA authority for the exact current v3 policy scope.

    The RSA public key and owner identity are first-import bootstrap capabilities;
    only the detached capture timestamp and signature are read per invocation.
    """

    modulus_text = _bootstrap_modulus_text
    exponent_text = _bootstrap_exponent_text
    provider_text = _bootstrap_provider_text
    principal_text = _bootstrap_principal_text
    captured_raw = _getenv(_captured_env)
    signature_text = _getenv(_signature_env)
    if not all(
        (modulus_text, exponent_text, provider_text, principal_text, captured_raw, signature_text)
    ):
        raise _error("payoff owner-policy RSA authority configuration is incomplete")

    if type(modulus_text) is not str or modulus_text != modulus_text.lower():
        raise _error("payoff owner-policy RSA modulus must be canonical lowercase hex")
    try:
        modulus = int(modulus_text, 16)
    except ValueError as exc:
        raise _error("payoff owner-policy RSA modulus must be hexadecimal") from exc
    if format(modulus, "x") != modulus_text or modulus.bit_length() < 2048 or modulus % 2 == 0:
        raise _error("payoff owner-policy RSA modulus must be canonical odd >=2048-bit hex")

    if type(exponent_text) is not str or not exponent_text.isdigit():
        raise _error("payoff owner-policy RSA exponent must be canonical decimal")
    exponent = int(exponent_text, 10)
    if str(exponent) != exponent_text or exponent < 3 or exponent % 2 == 0:
        raise _error("payoff owner-policy RSA exponent must be canonical odd decimal >=3")

    provider = _opaque_ref(provider_text, _provider_env)
    principal = _sha(principal_text, _principal_env)
    captured = _canonical_capture_impl(captured_raw, _captured_env)
    payload = _payload_impl(
        document,
        provider=provider,
        principal_sha256=principal,
        captured_at_utc=captured,
    )

    key_bytes = (modulus.bit_length() + 7) // 8
    if type(signature_text) is not str or signature_text != signature_text.lower():
        raise _error("payoff owner-policy RSA signature must be canonical lowercase hex")
    if len(signature_text) != key_bytes * 2:
        raise _error("payoff owner-policy RSA signature length does not match modulus")
    try:
        signature = int(signature_text, 16)
    except ValueError as exc:
        raise _error("payoff owner-policy RSA signature must be hexadecimal") from exc
    if signature >= modulus:
        raise _error("payoff owner-policy RSA signature is out of range")

    digest = _sha256(_canonical_bytes_impl(payload)).digest()
    digest_info = _digestinfo_prefix + digest
    padding_length = key_bytes - len(digest_info) - 3
    if padding_length < 8:
        raise _error("payoff owner-policy RSA modulus is too small for SHA-256 signature")
    expected = b"\x00\x01" + (b"\xff" * padding_length) + b"\x00" + digest_info
    recovered = pow(signature, exponent, modulus).to_bytes(key_bytes, "big")
    if not _compare_digest(recovered, expected):
        raise _error("payoff owner-policy RSA signature mismatch")

    captured_dt = _timestamp(captured, _captured_env)
    now = _datetime_now(_timezone_utc).replace(microsecond=0)
    age = (now - captured_dt).total_seconds()
    if age < 0:
        raise _error("payoff owner-policy authority capture is in the future")
    if age > _max_age:
        raise _error("payoff owner-policy authority capture is stale")
    return payload


class _VerifyOnlyAuthorityModule(types.ModuleType):
    """Keep verifier and first-import trust anchor stable under ordinary mutation."""

    _LOCKED_NAMES = frozenset(
        {
            "verify_current_policy_authority",
            "_BOOTSTRAP_MODULUS_TEXT",
            "_BOOTSTRAP_EXPONENT_TEXT",
            "_BOOTSTRAP_PROVIDER_TEXT",
            "_BOOTSTRAP_PRINCIPAL_TEXT",
        }
    )

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._LOCKED_NAMES and name in self.__dict__:
            return
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if name in self._LOCKED_NAMES:
            raise AttributeError(f"{name} is a retained verify-only authority capability")
        super().__delattr__(name)


sys.modules[__name__].__class__ = _VerifyOnlyAuthorityModule


__all__ = [
    "AUTHORITY_PURPOSE",
    "MAX_AUTHORITY_AGE_SECONDS",
    "MODULUS_ENV",
    "EXPONENT_ENV",
    "PROVIDER_ENV",
    "PRINCIPAL_ENV",
    "CAPTURED_AT_ENV",
    "SIGNATURE_ENV",
    "policy_authority_scope",
    "policy_authority_scope_sha256",
    "verify_current_policy_authority",
]
