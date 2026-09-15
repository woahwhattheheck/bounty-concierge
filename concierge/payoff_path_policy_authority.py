# SPDX-License-Identifier: MIT
"""Host-held owner authority for payoff budget policy.

The v3 continuity ledger keeps policy and effort facts append-only, but its work
document is caller-controlled. A caller must not be able to turn a self-authored
higher BUDGET_POLICY generation into READY owner-review evidence.

Every ordinary/public v3 compilation and verification requires a detached HMAC
authorization supplied only through host environment. The signed scope binds the
exact canonical policy chain, ledger generation, prior-receipt anchor, and
work/opportunity scope. No key, signature, provider, principal, capture time, or
unsigned-bypass switch is accepted from the work document or CLI.

The enforcement call lives inside the sole v3 compiler/verifier implementation,
not in a mutable one-time monkey patch. Reloading the v3 module therefore reloads
the guard with the implementation instead of restoring an unauthenticated raw path.
Historical semantic tests may mock the verifier in-process; production runtime code
contains no configuration flag that disables owner authentication.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from . import payoff_path_gate_core as _core
from . import payoff_path_policy_v3 as _v3

AUTHORITY_PURPOSE = "bounty-payoff-owner-policy-authority/v1"
MAX_AUTHORITY_AGE_SECONDS = 300

KEY_ENV = "BOUNTY_PAYOFF_POLICY_HMAC_KEY_HEX"
PROVIDER_ENV = "BOUNTY_PAYOFF_POLICY_AUTHORIZED_PROVIDER"
PRINCIPAL_ENV = "BOUNTY_PAYOFF_POLICY_AUTHORIZED_PRINCIPAL_SHA256"
CAPTURED_AT_ENV = "BOUNTY_PAYOFF_POLICY_AUTHORITY_CAPTURED_AT_UTC"
SIGNATURE_ENV = "BOUNTY_PAYOFF_POLICY_AUTHORITY_SIGNATURE_SHA256"


def _canonical_bytes(value: Any) -> bytes:
    import json

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _host_identity() -> tuple[bytes, str, str]:
    key_text = os.environ.get(KEY_ENV)
    provider_text = os.environ.get(PROVIDER_ENV)
    principal_text = os.environ.get(PRINCIPAL_ENV)
    if not key_text or not provider_text or not principal_text:
        raise _core.PayoffPathError("payoff owner-policy host configuration is incomplete")
    if len(key_text) < 64 or len(key_text) % 2:
        raise _core.PayoffPathError(
            "payoff owner-policy HMAC key must be at least 32 bytes of hex"
        )
    try:
        key = bytes.fromhex(key_text)
    except ValueError as exc:
        raise _core.PayoffPathError(
            "payoff owner-policy HMAC key must be hexadecimal"
        ) from exc
    if len(key) < 32:
        raise _core.PayoffPathError(
            "payoff owner-policy HMAC key must be at least 32 bytes"
        )
    provider = _core._opaque_ref(provider_text, PROVIDER_ENV)
    principal = _core._sha(principal_text, PRINCIPAL_ENV)
    return key, provider, principal


def _canonical_capture(value: Any, name: str) -> str:
    if type(value) is not str:
        raise _core.PayoffPathError(f"{name} must be canonical UTC text")
    parsed = _core._timestamp(value, name)
    rendered = _core._render_timestamp(parsed)
    if rendered != value:
        raise _core.PayoffPathError(f"{name} must be canonical UTC text")
    return rendered


def policy_authority_scope(document: Any) -> dict[str, Any]:
    """Return the canonical owner-policy scope a trusted host authorizes."""

    if type(document) is not dict or document.get("schema") != _v3.WORK_SCHEMA:
        raise _core.PayoffPathError(
            f"current owner-policy authority requires {_v3.WORK_SCHEMA} input"
        )
    normalized = _v3._normalize_document(document)
    continuity = normalized["continuity"]
    policies = [
        deepcopy(event)
        for event in continuity["events"]
        if event["kind"] == "BUDGET_POLICY"
    ]
    if not policies:
        raise _core.PayoffPathError("current v3 document has no owner budget policy")
    return {
        "purpose": AUTHORITY_PURPOSE,
        "ledger_id": continuity["ledger_id"],
        "generation": continuity["generation"],
        "previous_receipt_sha256": continuity["previous_receipt_sha256"],
        "work_scope_sha256": _v3._work_scope_sha256(normalized["work_items"]),
        "policy_events": policies,
    }


def policy_authority_scope_sha256(document: Any) -> str:
    return _core._digest(policy_authority_scope(document))


def _signature_payload(
    document: Any,
    *,
    provider: str,
    principal_sha256: str,
    captured_at_utc: str,
) -> dict[str, Any]:
    return {
        "purpose": AUTHORITY_PURPOSE,
        "provider": provider,
        "principal_sha256": principal_sha256,
        "captured_at_utc": captured_at_utc,
        "policy_scope_sha256": policy_authority_scope_sha256(document),
    }


def _sign_current_policy_authority_for_host_fixture(
    document: Any,
    *,
    captured_at_utc: str | None = None,
) -> dict[str, str]:
    """Private trusted-host/test fixture signer; never exported through gate."""

    key, provider, principal = _host_identity()
    if captured_at_utc is None:
        captured_at_utc = _core._render_timestamp(
            datetime.now(timezone.utc).replace(microsecond=0)
        )
    captured = _canonical_capture(captured_at_utc, "captured_at_utc")
    payload = _signature_payload(
        document,
        provider=provider,
        principal_sha256=principal,
        captured_at_utc=captured,
    )
    signature = hmac.new(key, _canonical_bytes(payload), hashlib.sha256).hexdigest()
    return {
        CAPTURED_AT_ENV: captured,
        SIGNATURE_ENV: signature,
    }


def verify_current_policy_authority(document: Any) -> dict[str, Any]:
    """Verify detached host authority for the exact v3 policy scope."""

    key, provider, principal = _host_identity()
    captured_raw = os.environ.get(CAPTURED_AT_ENV)
    signature_raw = os.environ.get(SIGNATURE_ENV)
    if not captured_raw or not signature_raw:
        raise _core.PayoffPathError(
            "current payoff owner policy requires detached host authority"
        )
    captured = _canonical_capture(captured_raw, CAPTURED_AT_ENV)
    signature = _core._sha(signature_raw, SIGNATURE_ENV)
    payload = _signature_payload(
        document,
        provider=provider,
        principal_sha256=principal,
        captured_at_utc=captured,
    )
    expected = hmac.new(key, _canonical_bytes(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise _core.PayoffPathError("payoff owner-policy host HMAC mismatch")

    captured_dt = _core._timestamp(captured, CAPTURED_AT_ENV)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    age = (now - captured_dt).total_seconds()
    if age < 0:
        raise _core.PayoffPathError("payoff owner-policy authority capture is in the future")
    if age > MAX_AUTHORITY_AGE_SECONDS:
        raise _core.PayoffPathError("payoff owner-policy authority capture is stale")
    return payload


__all__ = [
    "AUTHORITY_PURPOSE",
    "MAX_AUTHORITY_AGE_SECONDS",
    "KEY_ENV",
    "PROVIDER_ENV",
    "PRINCIPAL_ENV",
    "CAPTURED_AT_ENV",
    "SIGNATURE_ENV",
    "policy_authority_scope",
    "policy_authority_scope_sha256",
    "verify_current_policy_authority",
]
