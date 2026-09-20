# SPDX-License-Identifier: MIT
"""Live fixed-cash composition for GrantFox activation receipts.

The GrantFox lifecycle stack intentionally keeps provider/source/dependency
evidence separate from canonical bounty economics. This module joins those
surfaces at dispatch time. A lifecycle receipt is actionable only when the same
GitHub issue also has a *currently re-verified* fixed-USD admission of at least
$50 routed to ``main_bounty_queue``.

This module is advisory-only. It does not apply to GrantFox, mutate GitHub,
submit pull requests, contact sponsors, move funds, or grant any provider-side
authority.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import requests

from .bounty_live_cash_admission import verify_live_cash_receipt
from .grantfox_activation_gate import verify_activation_receipt


SCHEMA = "grantfox-live-cash-activation/v1"
RECEIPT_SCHEMA = "grantfox-live-cash-activation-receipt/v1"
ACTIVE_CASH_DISPOSITION = "ACTIVE_REVIEW"
ACTIVE_CASH_ROUTE = "main_bounty_queue"
ACTIVE_USD_FLOOR = Decimal("50")
PASSTHROUGH = frozenset(
    {
        "APPLY_ELIGIBLE",
        "REPLAN_BEFORE_APPLY",
        "WAIT_ASSIGNMENT",
        "WAIT_DEPENDENCIES",
        "IMPLEMENT_ASSIGNED_SCOPE",
    }
)
ACTIVATION_HOLDS = frozenset(
    {
        "HOLD_SOURCE",
        "HOLD_DEPENDENCIES",
        "HOLD_RECONCILE",
    }
)
AUTHORITY = {
    "advisory_only": True,
    "provider_application_authority": False,
    "implementation_write_authority": False,
    "submission_authority": False,
    "adjudication_authority": False,
    "outbound_contact_authority": False,
    "payment_or_wallet_authority": False,
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REPO = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)


class GrantFoxLiveCashActivationInputError(ValueError):
    """Raised when activation/cash evidence cannot be composed safely."""


def _hash(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _obj(value: Any, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise GrantFoxLiveCashActivationInputError(f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise GrantFoxLiveCashActivationInputError(
            f"{field} must be a non-empty trimmed string"
        )
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GrantFoxLiveCashActivationInputError(
            f"{field} must be a positive integer"
        )
    return value


def _decimal(value: Any, field: str) -> Decimal:
    raw = _text(value, field)
    try:
        parsed = Decimal(raw)
    except InvalidOperation as exc:
        raise GrantFoxLiveCashActivationInputError(
            f"{field} must be an exact decimal string"
        ) from exc
    if not parsed.is_finite() or parsed < 0:
        raise GrantFoxLiveCashActivationInputError(
            f"{field} must be finite and non-negative"
        )
    return parsed


def _activation_identity(receipt: dict[str, Any]) -> tuple[str, str, int, str]:
    identity = _obj(receipt.get("identity"), "activation_receipt.identity")
    owner = _text(
        identity.get("owner"), "activation_receipt.identity.owner"
    ).casefold()
    repo = _text(
        identity.get("repo"), "activation_receipt.identity.repo"
    ).casefold()
    issue_number = _positive_int(
        identity.get("issue_number"),
        "activation_receipt.identity.issue_number",
    )
    actor = _text(
        identity.get("actor_login"),
        "activation_receipt.identity.actor_login",
    ).casefold()
    return owner, repo, issue_number, actor


def _cash_identity(receipt: dict[str, Any]) -> tuple[str, str, int]:
    identity = _obj(receipt.get("identity"), "live_cash_receipt.identity")
    full_name = _text(
        identity.get("repo"), "live_cash_receipt.identity.repo"
    )
    if _REPO.fullmatch(full_name) is None:
        raise GrantFoxLiveCashActivationInputError(
            "live_cash_receipt.identity.repo must be owner/name"
        )
    owner, repo = full_name.split("/", 1)
    issue_number = _positive_int(
        identity.get("issue_number"),
        "live_cash_receipt.identity.issue_number",
    )
    canonical_url = _text(
        identity.get("canonical_issue_url"),
        "live_cash_receipt.identity.canonical_issue_url",
    )
    expected = f"https://github.com/{owner}/{repo}/issues/{issue_number}"
    if canonical_url.casefold() != expected.casefold():
        raise GrantFoxLiveCashActivationInputError(
            "live_cash_receipt canonical URL does not match its repo/issue identity"
        )
    return owner.casefold(), repo.casefold(), issue_number


def _cash_projection(receipt: dict[str, Any]) -> dict[str, Any]:
    economics = _obj(
        receipt.get("economics"), "live_cash_receipt.economics"
    )
    amount_raw = economics.get("fixed_amount")
    amount = (
        None
        if amount_raw is None
        else _decimal(amount_raw, "live_cash_receipt.economics.fixed_amount")
    )
    currency = economics.get("currency")
    if currency is not None and type(currency) is not str:
        raise GrantFoxLiveCashActivationInputError(
            "live_cash_receipt.economics.currency must be a string or null"
        )
    fixed_semantics = economics.get("fixed_semantics")
    if type(fixed_semantics) is not bool:
        raise GrantFoxLiveCashActivationInputError(
            "live_cash_receipt.economics.fixed_semantics must be a boolean"
        )
    disposition = _text(
        receipt.get("disposition"), "live_cash_receipt.disposition"
    )
    route = receipt.get("route")
    if route is not None and (
        type(route) is not str or not route or route != route.strip()
    ):
        raise GrantFoxLiveCashActivationInputError(
            "live_cash_receipt.route must be a non-empty trimmed string or null"
        )
    return {
        "disposition": disposition,
        "route": route,
        "currency": currency,
        "fixed_amount": str(amount) if amount is not None else None,
        "fixed_semantics": fixed_semantics,
        "_amount": amount,
    }


def compile_live_cash_activation(
    request: dict[str, Any],
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
    saturation_threshold: int = 4,
    operator_login: str | None = None,
) -> dict[str, Any]:
    """Compose GrantFox lifecycle evidence with live canonical cash admission.

    ``verify_live_cash_receipt`` intentionally re-reads GitHub, so compiling an
    old receipt after the canonical issue changes fails closed rather than
    replaying historical economics.
    """
    request = _obj(request, "request")
    if request.get("schema") != SCHEMA:
        raise GrantFoxLiveCashActivationInputError(
            f"schema must equal {SCHEMA}"
        )

    activation = _obj(
        request.get("activation_receipt"), "activation_receipt"
    )
    cash = _obj(request.get("live_cash_receipt"), "live_cash_receipt")

    if not verify_activation_receipt(activation):
        raise GrantFoxLiveCashActivationInputError(
            "activation_receipt does not verify semantically"
        )
    if not verify_live_cash_receipt(
        cash,
        token,
        session=session,
        max_pages=max_pages,
        saturation_threshold=saturation_threshold,
        operator_login=operator_login,
    ):
        raise GrantFoxLiveCashActivationInputError(
            "live_cash_receipt does not re-verify against current canonical GitHub state"
        )

    owner, repo, issue_number, actor = _activation_identity(activation)
    cash_owner, cash_repo, cash_issue_number = _cash_identity(cash)
    if (owner, repo, issue_number) != (
        cash_owner,
        cash_repo,
        cash_issue_number,
    ):
        raise GrantFoxLiveCashActivationInputError(
            "activation and live cash receipts identify different issues"
        )

    activation_disposition = _text(
        activation.get("disposition"), "activation_receipt.disposition"
    )
    if (
        activation_disposition not in PASSTHROUGH
        and activation_disposition not in ACTIVATION_HOLDS
    ):
        raise GrantFoxLiveCashActivationInputError(
            "activation_receipt emitted an unsupported disposition"
        )

    cash_view = _cash_projection(cash)
    amount = cash_view.pop("_amount")
    reasons: list[str] = []
    economics_active = True

    if cash_view["disposition"] != ACTIVE_CASH_DISPOSITION:
        economics_active = False
        reasons.append("LIVE_CASH_NOT_ACTIVE_REVIEW")
    if cash_view["route"] != ACTIVE_CASH_ROUTE:
        economics_active = False
        reasons.append("LIVE_CASH_NOT_MAIN_BOUNTY_QUEUE")
    if cash_view["currency"] != "USD":
        economics_active = False
        reasons.append("LIVE_CASH_NOT_FIXED_USD")
    if not cash_view["fixed_semantics"]:
        economics_active = False
        reasons.append("LIVE_CASH_NOT_FIXED_SEMANTICS")
    if amount is None or amount < ACTIVE_USD_FLOOR:
        economics_active = False
        reasons.append("LIVE_CASH_BELOW_ACTIVE_FLOOR")

    if activation_disposition in ACTIVATION_HOLDS:
        disposition = "HOLD_GRANTFOX_ACTIVATION"
        reasons.insert(0, "GRANTFOX_ACTIVATION_NOT_ACTIONABLE")
        next_action = "RESOLVE_GRANTFOX_ACTIVATION_BEFORE_DISPATCH"
    elif not economics_active:
        disposition = "HOLD_ECONOMICS"
        next_action = "REMOVE_FROM_ACTIVE_QUEUE_OR_REFRESH_LIVE_CASH"
    else:
        disposition = activation_disposition
        next_action = _text(
            activation.get("advisory_next_action"),
            "activation_receipt.advisory_next_action",
        )

    activation_digest = _text(
        activation.get("activation_receipt_sha256"),
        "activation_receipt.activation_receipt_sha256",
    )
    cash_digest = _text(
        cash.get("receipt_sha256"), "live_cash_receipt.receipt_sha256"
    )
    if _SHA256.fullmatch(activation_digest) is None:
        raise GrantFoxLiveCashActivationInputError(
            "activation receipt digest must be lowercase sha256"
        )
    if _SHA256.fullmatch(cash_digest) is None:
        raise GrantFoxLiveCashActivationInputError(
            "live cash receipt digest must be lowercase sha256"
        )

    body = {
        "schema": RECEIPT_SCHEMA,
        "disposition": disposition,
        "advisory_next_action": next_action,
        "reason_codes": reasons,
        "identity": {
            "owner": owner,
            "repo": repo,
            "issue_number": issue_number,
            "actor_login": actor,
        },
        "economics": {
            "cash_disposition": cash_view["disposition"],
            "cash_route": cash_view["route"],
            "currency": cash_view["currency"],
            "fixed_amount": cash_view["fixed_amount"],
            "fixed_semantics": cash_view["fixed_semantics"],
            "required_active_floor_usd": "50",
        },
        "activation": {
            "disposition": activation_disposition,
        },
        "anchors": {
            "activation_receipt_sha256": activation_digest,
            "live_cash_receipt_sha256": cash_digest,
        },
        "evidence": {
            "activation_receipt": activation,
            "live_cash_receipt": cash,
        },
        "authority": dict(AUTHORITY),
    }
    return {**body, "receipt_sha256": _hash(body)}


def verify_live_cash_activation_receipt(
    receipt: dict[str, Any],
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
    saturation_threshold: int = 4,
    operator_login: str | None = None,
) -> bool:
    """Verify integrity, nested semantics, and *current* live cash evidence."""
    if type(receipt) is not dict:
        return False
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("authority") != AUTHORITY
    ):
        return False
    digest = receipt.get("receipt_sha256")
    if type(digest) is not str or _SHA256.fullmatch(digest) is None:
        return False
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    if _hash(body) != digest:
        return False

    evidence = receipt.get("evidence")
    if type(evidence) is not dict or set(evidence) != {
        "activation_receipt",
        "live_cash_receipt",
    }:
        return False
    activation = evidence.get("activation_receipt")
    cash = evidence.get("live_cash_receipt")
    if type(activation) is not dict or type(cash) is not dict:
        return False

    try:
        expected = compile_live_cash_activation(
            {
                "schema": SCHEMA,
                "activation_receipt": activation,
                "live_cash_receipt": cash,
            },
            token,
            session=session,
            max_pages=max_pages,
            saturation_threshold=saturation_threshold,
            operator_login=operator_login,
        )
    except (
        GrantFoxLiveCashActivationInputError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return False
    return expected == receipt


def format_summary(receipt: dict[str, Any]) -> str:
    identity = receipt["identity"]
    reasons = ",".join(receipt["reason_codes"]) or "none"
    return (
        f"{identity['owner']}/{identity['repo']}#{identity['issue_number']} "
        f"activation={receipt['activation']['disposition']} "
        f"cash={receipt['economics']['cash_disposition']} "
        f"amount_usd={receipt['economics']['fixed_amount'] or 'none'} "
        f"disposition={receipt['disposition']} "
        f"reasons={reasons}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.grantfox_live_cash_activation",
        description=(
            "Compose GrantFox activation with live canonical >=$50 fixed-USD "
            "admission. Verification re-reads GitHub."
        ),
    )
    parser.add_argument("request", help="request JSON path, or - for stdin")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--saturation-threshold", type=int, default=4)
    parser.add_argument("--operator-login")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.request == "-":
            import sys

            request = json.load(sys.stdin)
        else:
            request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        receipt = compile_live_cash_activation(
            request,
            max_pages=args.max_pages,
            saturation_threshold=args.saturation_threshold,
            operator_login=args.operator_login,
        )
    except (
        OSError,
        json.JSONDecodeError,
        GrantFoxLiveCashActivationInputError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(receipt, indent=2, sort_keys=True)
        if args.json
        else format_summary(receipt)
    )
    return 0 if receipt["disposition"] in PASSTHROUGH else 2


if __name__ == "__main__":
    raise SystemExit(main())
