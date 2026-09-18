"""Host authority for sponsor-origin adjudication events.

The compiler's ordinary manifest is caller-controlled.  This module prevents that
caller from turning a self-authored source_ref/source_sha256 pair into sponsor
verification, reward, or payment posture.  A non-empty sponsor-event generation
must be attested by the credential-owning host with a key and identity supplied
through host environment, never caller arguments.

Historical reports carry a second host HMAC over the complete compiled report
projection.  That binding chains the exact retained report bytes back to the
signed authority-free manifest generation, so a caller cannot edit a derived
status/action or other non-event output, recompute the public report hash, and
keep sponsor authority.

The test-only unsigned switch exists solely so the predecessor semantic suite can
exercise its historical state machine.  It is an explicit host-environment
choice; production entrypoints must leave it unset.
"""
from __future__ import annotations

import hashlib
import hmac
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Sequence

from .common import (
    AdjudicationError,
    _canonical_bytes,
    _parse_utc,
    _require_dict,
    _require_exact_keys,
    _require_hex64,
    _require_id,
    _require_text,
    _sha256_obj,
    os,
)

AUTHORITY_SCHEMA_VERSION = 1
AUTHORITY_PURPOSE = "bounty-sponsor-adjudication-event-authority/v1"
REPORT_BINDING_SCHEMA_VERSION = 1
REPORT_BINDING_PURPOSE = "bounty-sponsor-adjudication-report-generation-authority/v1"
MAX_AUTHORITY_AGE_SECONDS = 300
KEY_ENV = "BOUNTY_SPONSOR_ADJUDICATION_HMAC_KEY_HEX"
PROVIDER_ENV = "BOUNTY_SPONSOR_ADJUDICATION_AUTHORIZED_PROVIDER"
PRINCIPAL_ENV = "BOUNTY_SPONSOR_ADJUDICATION_AUTHORIZED_PRINCIPAL_SHA256"
TEST_UNSIGNED_ENV = "BOUNTY_SPONSOR_ADJUDICATION_TEST_ONLY_ALLOW_UNSIGNED"

_EVENT_KEYS = (
    "event_id",
    "event_type",
    "event_at",
    "source_ref",
    "source_sha256",
    "claim_unit_id",
    "finding_ids",
    "submission_ids",
    "collapsed_claim_unit_ids",
    "amount",
    "currency",
    "note",
)


def _test_unsigned_enabled() -> bool:
    return os.environ.get(TEST_UNSIGNED_ENV) == "1"


def event_scope_rows(events: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Return the exact sponsor-event projection carried by compiled reports."""
    rows: List[Dict[str, Any]] = []
    for event in events:
        row: Dict[str, Any] = {}
        for key in _EVENT_KEYS:
            if key in event:
                value = event[key]
                if key in {"finding_ids", "submission_ids", "collapsed_claim_unit_ids"}:
                    value = sorted(list(value))
                row[key] = deepcopy(value)
            else:
                row[key] = [] if key in {"finding_ids", "submission_ids", "collapsed_claim_unit_ids"} else None
        rows.append(row)
    rows.sort(key=lambda row: (row["event_at"], row["event_id"]))
    return rows


def _bare_program(program: Mapping[str, Any]) -> Dict[str, str]:
    return {
        "program_id": _require_id(program.get("program_id"), where="program.program_id"),
        "sponsor": _require_text(program.get("sponsor"), where="program.sponsor"),
        "source_ref": _require_text(program.get("source_ref"), where="program.source_ref"),
    }


def event_scope_sha256(program: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> str:
    return _sha256_obj({"program": _bare_program(program), "sponsor_events": event_scope_rows(events)})


def authority_free_manifest_sha256(manifest: Mapping[str, Any]) -> str:
    root = deepcopy(dict(manifest))
    root.pop("sponsor_authority", None)
    return _sha256_obj(root)


def _host_config() -> tuple[bytes, str, str]:
    key_text = os.environ.get(KEY_ENV)
    provider_text = os.environ.get(PROVIDER_ENV)
    principal_text = os.environ.get(PRINCIPAL_ENV)
    if not key_text or not provider_text or not principal_text:
        raise AdjudicationError("sponsor authority host configuration is incomplete")
    if len(key_text) < 64 or len(key_text) % 2:
        raise AdjudicationError("sponsor authority HMAC key must be at least 32 bytes of hex")
    try:
        key = bytes.fromhex(key_text)
    except ValueError as exc:
        raise AdjudicationError("sponsor authority HMAC key must be lowercase/uppercase hex") from exc
    if len(key) < 32:
        raise AdjudicationError("sponsor authority HMAC key must be at least 32 bytes")
    provider = _require_id(provider_text, where=PROVIDER_ENV)
    principal = _require_hex64(principal_text, where=PRINCIPAL_ENV)
    return key, provider, principal


def _normalize_authority(value: Any) -> Dict[str, Any]:
    obj = _require_dict(value, where="sponsor_authority")
    _require_exact_keys(
        obj,
        [
            "schema_version",
            "purpose",
            "provider",
            "principal_sha256",
            "captured_at",
            "event_scope_sha256",
            "manifest_sha256",
            "signature_sha256",
        ],
        where="sponsor_authority",
    )
    if type(obj["schema_version"]) is not int or obj["schema_version"] != AUTHORITY_SCHEMA_VERSION:
        raise AdjudicationError("sponsor_authority.schema_version mismatch")
    if obj["purpose"] != AUTHORITY_PURPOSE:
        raise AdjudicationError("sponsor_authority.purpose mismatch")
    provider = _require_id(obj["provider"], where="sponsor_authority.provider")
    principal = _require_hex64(obj["principal_sha256"], where="sponsor_authority.principal_sha256")
    captured_at = _require_text(obj["captured_at"], where="sponsor_authority.captured_at")
    _parse_utc(captured_at, where="sponsor_authority.captured_at")
    scope_sha = _require_hex64(obj["event_scope_sha256"], where="sponsor_authority.event_scope_sha256")
    manifest_sha = _require_hex64(obj["manifest_sha256"], where="sponsor_authority.manifest_sha256")
    signature = _require_hex64(obj["signature_sha256"], where="sponsor_authority.signature_sha256")
    return {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "purpose": AUTHORITY_PURPOSE,
        "provider": provider,
        "principal_sha256": principal,
        "captured_at": captured_at,
        "event_scope_sha256": scope_sha,
        "manifest_sha256": manifest_sha,
        "signature_sha256": signature,
    }


def _signature_core(authority: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: deepcopy(value) for key, value in authority.items() if key != "signature_sha256"}


def _verify_signature(authority: Mapping[str, Any]) -> None:
    key, provider, principal = _host_config()
    if authority["provider"] != provider:
        raise AdjudicationError("sponsor authority provider is not host-authorized")
    if not hmac.compare_digest(authority["principal_sha256"], principal):
        raise AdjudicationError("sponsor authority principal is not host-authorized")
    expected = hmac.new(key, _canonical_bytes(_signature_core(authority)), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(authority["signature_sha256"], expected):
        raise AdjudicationError("sponsor authority HMAC mismatch")


def _require_fresh(authority: Mapping[str, Any]) -> None:
    captured = _parse_utc(authority["captured_at"], where="sponsor_authority.captured_at")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    age = (now - captured).total_seconds()
    if age < 0:
        raise AdjudicationError("sponsor authority capture is in the future")
    if age > MAX_AUTHORITY_AGE_SECONDS:
        raise AdjudicationError("sponsor authority capture is stale")


def _normalize_report_binding(value: Any) -> Dict[str, Any]:
    obj = _require_dict(value, where="sponsor_authority_binding")
    _require_exact_keys(
        obj,
        [
            "schema_version",
            "purpose",
            "manifest_sha256",
            "authority_sha256",
            "report_projection_sha256",
            "signature_sha256",
        ],
        where="sponsor_authority_binding",
    )
    if type(obj["schema_version"]) is not int or obj["schema_version"] != REPORT_BINDING_SCHEMA_VERSION:
        raise AdjudicationError("sponsor_authority_binding.schema_version mismatch")
    if obj["purpose"] != REPORT_BINDING_PURPOSE:
        raise AdjudicationError("sponsor_authority_binding.purpose mismatch")
    return {
        "schema_version": REPORT_BINDING_SCHEMA_VERSION,
        "purpose": REPORT_BINDING_PURPOSE,
        "manifest_sha256": _require_hex64(obj["manifest_sha256"], where="sponsor_authority_binding.manifest_sha256"),
        "authority_sha256": _require_hex64(obj["authority_sha256"], where="sponsor_authority_binding.authority_sha256"),
        "report_projection_sha256": _require_hex64(obj["report_projection_sha256"], where="sponsor_authority_binding.report_projection_sha256"),
        "signature_sha256": _require_hex64(obj["signature_sha256"], where="sponsor_authority_binding.signature_sha256"),
    }


def _report_binding_signature_core(binding: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: deepcopy(value) for key, value in binding.items() if key != "signature_sha256"}


def _verify_report_binding_signature(binding: Mapping[str, Any]) -> None:
    key, _, _ = _host_config()
    expected = hmac.new(key, _canonical_bytes(_report_binding_signature_core(binding)), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(binding["signature_sha256"], expected):
        raise AdjudicationError("sponsor report generation HMAC mismatch")


def verify_manifest_authority(
    manifest: Mapping[str, Any],
    program: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    authority_value: Any,
) -> Dict[str, Any] | None:
    """Verify current host authority before caller events may drive state."""
    if not events:
        if authority_value is not None:
            raise AdjudicationError("sponsor_authority is forbidden when sponsor_events is empty")
        return None
    if _test_unsigned_enabled():
        return None
    if authority_value is None:
        _host_config()
        raise AdjudicationError("non-empty sponsor_events require host sponsor_authority")
    authority = _normalize_authority(authority_value)
    expected_scope = event_scope_sha256(program, events)
    if not hmac.compare_digest(authority["event_scope_sha256"], expected_scope):
        raise AdjudicationError("sponsor authority event scope mismatch")
    expected_manifest = authority_free_manifest_sha256(manifest)
    if not hmac.compare_digest(authority["manifest_sha256"], expected_manifest):
        raise AdjudicationError("sponsor authority manifest generation mismatch")
    _verify_signature(authority)
    _require_fresh(authority)
    return authority


def bind_report_generation(program: Mapping[str, Any], report_projection: Mapping[str, Any]) -> Dict[str, Any] | None:
    """Bind a compiled report projection to the exact signed input generation."""
    if _test_unsigned_enabled():
        return None
    authority = _normalize_authority(program.get("sponsor_authority"))
    _verify_signature(authority)
    key, _, _ = _host_config()
    binding = {
        "schema_version": REPORT_BINDING_SCHEMA_VERSION,
        "purpose": REPORT_BINDING_PURPOSE,
        "manifest_sha256": authority["manifest_sha256"],
        "authority_sha256": _sha256_obj(authority),
        "report_projection_sha256": _sha256_obj(report_projection),
    }
    binding["signature_sha256"] = hmac.new(key, _canonical_bytes(binding), hashlib.sha256).hexdigest()
    return binding


def verify_report_authority(
    program: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    report_binding_value: Any,
    report_projection: Mapping[str, Any],
) -> Dict[str, Any] | None:
    """Verify retained sponsor authority and the exact historical report generation.

    Historical verification intentionally does not impose current freshness on the
    original provider capture.  It does, however, require both the original host
    HMAC and the host HMAC over the complete compiled report projection.
    """
    if not events:
        if "sponsor_authority" in program:
            raise AdjudicationError("report has sponsor authority without sponsor events")
        if report_binding_value is not None:
            raise AdjudicationError("report has sponsor authority binding without sponsor events")
        return None
    if _test_unsigned_enabled():
        return None
    authority = _normalize_authority(program.get("sponsor_authority"))
    expected_scope = event_scope_sha256(program, events)
    if not hmac.compare_digest(authority["event_scope_sha256"], expected_scope):
        raise AdjudicationError("retained sponsor authority event scope mismatch")
    _verify_signature(authority)

    binding = _normalize_report_binding(report_binding_value)
    if not hmac.compare_digest(binding["manifest_sha256"], authority["manifest_sha256"]):
        raise AdjudicationError("retained sponsor report manifest binding mismatch")
    expected_authority_sha = _sha256_obj(authority)
    if not hmac.compare_digest(binding["authority_sha256"], expected_authority_sha):
        raise AdjudicationError("retained sponsor report authority binding mismatch")
    expected_projection_sha = _sha256_obj(report_projection)
    if not hmac.compare_digest(binding["report_projection_sha256"], expected_projection_sha):
        raise AdjudicationError("retained sponsor report generation binding mismatch")
    _verify_report_binding_signature(binding)
    return authority


def sign_for_test_or_host_fixture(
    manifest: Mapping[str, Any],
    program: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    captured_at: str,
) -> Dict[str, Any]:
    """Create an attestation using host env; intended for trusted adapters/tests only.

    Production ingestion must generate this only after the credential-owning host
    has reacquired the sponsor evidence.  Exporting this helper does not grant a
    caller authority because the HMAC key and authorized identity remain host env.
    """
    key, provider, principal = _host_config()
    _parse_utc(captured_at, where="captured_at")
    authority = {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "purpose": AUTHORITY_PURPOSE,
        "provider": provider,
        "principal_sha256": principal,
        "captured_at": captured_at,
        "event_scope_sha256": event_scope_sha256(program, events),
        "manifest_sha256": authority_free_manifest_sha256(manifest),
    }
    authority["signature_sha256"] = hmac.new(key, _canonical_bytes(authority), hashlib.sha256).hexdigest()
    return authority


__all__ = [
    "AUTHORITY_SCHEMA_VERSION",
    "AUTHORITY_PURPOSE",
    "REPORT_BINDING_SCHEMA_VERSION",
    "REPORT_BINDING_PURPOSE",
    "MAX_AUTHORITY_AGE_SECONDS",
    "KEY_ENV",
    "PROVIDER_ENV",
    "PRINCIPAL_ENV",
    "TEST_UNSIGNED_ENV",
    "event_scope_rows",
    "event_scope_sha256",
    "authority_free_manifest_sha256",
    "verify_manifest_authority",
    "bind_report_generation",
    "verify_report_authority",
    "sign_for_test_or_host_fixture",
]
