# SPDX-License-Identifier: MIT
"""Fail-closed hardening for the additive claim/work live authority surface.

The original claim-work primitive deliberately remains byte-for-byte attributable
to its source owner.  This module installs the two authority separations required
before that primitive can be used as durable revenue evidence:

* relation authorization and retained-receipt authentication use independent
  env-only HMAC keys; and
* sponsor-adjudication's explicitly test-only unsigned mode is forbidden at the
  downstream claim/work authority boundary.

Installation is package-internal and idempotent.  No caller can provide a key,
transport, or authority override through this shim.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Mapping

from . import claim_work_authority as _cwa


RECEIPT_KEY_ENV = "BOUNTY_CLAIM_WORK_PROVIDER_RECEIPT_KEY_HEX"
SPONSOR_UNSIGNED_TEST_ENV = "BOUNTY_SPONSOR_ADJUDICATION_TEST_ONLY_ALLOW_UNSIGNED"
_RECEIPT_DOMAIN = b"bounty-claim-work-live-binding/v1\x00"

_ORIGINAL_VERIFIED_CONTEXT = _cwa._verified_context
_ORIGINAL_VERIFY_RECEIPT = _cwa.verify_claim_work_receipt


def _require_authenticated_sponsor_mode() -> None:
    # Match the upstream switch's exact enabling semantics.  A literal "0" is
    # commonly present in deterministic host/test environments and does not
    # enable unsigned adjudication; "1" does and must never cross this boundary.
    if os.environ.get(SPONSOR_UNSIGNED_TEST_ENV) == "1":
        raise _cwa.ClaimWorkAuthorityError(
            "sponsor adjudication unsigned-test mode is forbidden for claim-work authority"
        )


def _receipt_key() -> bytes:
    raw = os.environ.get(RECEIPT_KEY_ENV)
    if raw is None or not _cwa._SHA256_RE.fullmatch(raw):
        raise _cwa.ClaimWorkAuthorityError(
            f"{RECEIPT_KEY_ENV} must be 64 lowercase hex characters"
        )
    receipt_key = bytes.fromhex(raw)
    relation_key = _cwa._host_key()
    if hmac.compare_digest(receipt_key, relation_key):
        raise _cwa.ClaimWorkAuthorityError(
            "claim-work relation and provider-receipt HMAC keys must be distinct"
        )
    return receipt_key


def _receipt_hmac(core: Mapping[str, Any]) -> str:
    return hmac.new(
        _receipt_key(),
        _RECEIPT_DOMAIN + _cwa._canonical_json(core),
        hashlib.sha256,
    ).hexdigest()


def _verified_context(report_raw: Any, claim_unit_id_raw: Any):
    _require_authenticated_sponsor_mode()
    return _ORIGINAL_VERIFIED_CONTEXT(report_raw, claim_unit_id_raw)


def _verify_claim_work_receipt(receipt: Any) -> bool:
    try:
        _require_authenticated_sponsor_mode()
    except _cwa.ClaimWorkAuthorityError:
        return False
    return _ORIGINAL_VERIFY_RECEIPT(receipt)


def install() -> None:
    """Install hardening into the already-loaded claim-work authority module."""
    if getattr(_cwa, "_CLAIM_WORK_HARDENING_INSTALLED", False):
        return
    _cwa.RECEIPT_KEY_ENV = RECEIPT_KEY_ENV
    _cwa.SPONSOR_UNSIGNED_TEST_ENV = SPONSOR_UNSIGNED_TEST_ENV
    _cwa._receipt_key = _receipt_key
    _cwa._receipt_hmac = _receipt_hmac
    _cwa._verified_context = _verified_context
    _cwa.verify_claim_work_receipt = _verify_claim_work_receipt
    _cwa._CLAIM_WORK_HARDENING_INSTALLED = True
