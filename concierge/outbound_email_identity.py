# SPDX-License-Identifier: MIT
"""Canonical email-touch identities for the outbound authority stack.

``outbound_singlewriter.normalize_identity`` is intentionally provider-neutral
and therefore does not guess whether two destination spellings are the same
mailbox.  Revenue email cannot safely use that generic boundary directly:
provider-truth dedupe already treats case/IDNA aliases as one recipient, and a
second worker must not be able to turn the same business touch into another
single-writer / Commons capability-lease domain merely by spelling the address
differently.

This module is deliberately small.  It reuses the exact recipient and offer-key
canonicalizers from ``outbound_dedupe`` and projects them into the existing
``OutboundIdentity`` shape.  It performs no provider I/O and grants no send,
contact, payment, or revenue authority.
"""
from __future__ import annotations

from datetime import datetime
import re
import unicodedata
from typing import Any, Optional

from .outbound_dedupe import OutboundDedupeInputError, _canonical_email, _offer_key
from .outbound_singlewriter import OutboundIdentity, OutboundSingleWriter, normalize_identity

EMAIL_TOUCH_PROVIDER = "email-outreach"
_ACTION_RE = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,127}$")


class EmailTouchIdentityError(ValueError):
    """Raised when an email touch cannot be mapped to one stable authority key."""


def _canonical_action(value: Any) -> str:
    if type(value) is not str:
        raise EmailTouchIdentityError("action must be a string")
    if value != value.strip() or not value:
        raise EmailTouchIdentityError("action must not contain edge whitespace")
    action = unicodedata.normalize("NFKC", value).casefold()
    if not _ACTION_RE.fullmatch(action):
        raise EmailTouchIdentityError(
            "action must normalize to lowercase letters, digits, . _ : / -"
        )
    return action


def normalize_email_touch_identity(
    *, recipient: str, offer_key: str, action: str
) -> OutboundIdentity:
    """Return one provider-neutral identity for an exact revenue email touch.

    Physical providers are intentionally excluded.  Gmail and another email
    transport are not independent authority domains for the same recipient,
    offer, and business action.

    ``recipient`` uses the exact operational identity policy already enforced
    by :mod:`concierge.outbound_dedupe`: case-folded local part plus IDNA/lower
    domain.  ``offer_key`` remains a caller-retained lowercase stable key, and
    ``action`` is NFKC/case-fold normalized to a compact machine token.
    """
    try:
        canonical_recipient = _canonical_email(recipient, field="recipient")
        canonical_offer = _offer_key(offer_key, field="offer_key")
    except OutboundDedupeInputError as exc:
        raise EmailTouchIdentityError(str(exc)) from exc

    canonical_action = _canonical_action(action)
    return normalize_identity(
        provider=EMAIL_TOUCH_PROVIDER,
        destination=canonical_recipient,
        thread=f"offer:{canonical_offer}",
        operation=canonical_action,
    )


def email_touch_operation_key(*, recipient: str, offer_key: str, action: str) -> str:
    """Return the SHA-256 operation key used by all email-touch authority layers."""
    return normalize_email_touch_identity(
        recipient=recipient,
        offer_key=offer_key,
        action=action,
    ).key


def acquire_email_touch(
    guard: OutboundSingleWriter,
    *,
    recipient: str,
    offer_key: str,
    action: str,
    owner: str,
    ttl_seconds: int = 300,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Acquire the existing local lease using the canonical email-touch domain.

    This is only the local idempotency/custody step.  Production outbound still
    requires provider-side DNR evidence, owner/content/route authority, Muse or
    equivalent fleet arbitration, and the Commons v2 capability-possession gate
    before any external provider mutation.
    """
    if not isinstance(guard, OutboundSingleWriter):
        raise EmailTouchIdentityError("guard must be an OutboundSingleWriter")
    identity = normalize_email_touch_identity(
        recipient=recipient,
        offer_key=offer_key,
        action=action,
    )
    return guard.acquire(
        **identity.canonical,
        owner=owner,
        ttl_seconds=ttl_seconds,
        now=now,
    )
