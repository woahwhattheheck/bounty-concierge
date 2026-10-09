# SPDX-License-Identifier: MIT
"""Draft evidence-fenced direct-creator settlement packets for Opire-listed work.

Opire confirmed on 2026-10-08 that it no longer manages claims or payments;
this tool never treats a listing, /try, /claim, merged PR, or sponsor statement
as bank-verified receipt. It has no network, provider or money-moving calls.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

SCHEMA = "opire-direct-creator-settlement/v1"
_ORIGINAL_ACTORS = frozenset({"woahwhattheheck", "tokenjunkielabs"})
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class SettlementInputError(ValueError):
    """An input receipt cannot safely identify an original contributor's work."""


def _text(value: Any, name: str, maximum: int = 512) -> str:
    if (not isinstance(value, str) or not value or value.strip() != value
            or "\x00" in value or len(value.encode("utf-8")) > maximum):
        raise SettlementInputError(f"{name} must be a bounded non-empty string")
    return value


def _canonical_url(raw: Any, repo: str, kind: str, issue_number: int | None = None) -> str:
    url = _text(raw, f"{kind}_url", maximum=1024)
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query
            or parsed.fragment or parsed.username or parsed.password):
        raise SettlementInputError(f"{kind}_url must be a canonical GitHub URL")
    pattern = rf"/{re.escape(repo)}/{('issues' if kind == 'issue' else 'pull')}/([1-9][0-9]*)"
    match = re.fullmatch(pattern, parsed.path, flags=re.IGNORECASE)
    if match is None or (issue_number is not None and int(match.group(1)) != issue_number):
        raise SettlementInputError(f"{kind}_url does not match the requested repository/issue")
    return url


def _external_evidence(value: Any, name: str) -> str | None:
    if value is None:
        return None
    url = _text(value, name, maximum=1024)
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or parsed.query):
        raise SettlementInputError(f"{name} requires an HTTPS source URL without credentials or tokens")
    return url


def _amount(value: Any) -> str:
    if type(value) not in (str, int, float) or isinstance(value, bool):
        raise SettlementInputError("advertised_usd must be a positive USD amount")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise SettlementInputError("advertised_usd is not numeric") from exc
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        raise SettlementInputError("advertised_usd must be positive with at most two decimals")
    return str(amount.quantize(Decimal("0.01")))


def compile_settlement_packet(payload: dict[str, Any]) -> dict[str, Any]:
    """Produce a direct creator *request*, not a registered platform claim or payout."""
    if not isinstance(payload, dict) or payload.get("platform") != "Opire":
        raise SettlementInputError("platform must be Opire")
    repo = _text(payload.get("repo"), "repo", maximum=180)
    if _REPO.fullmatch(repo) is None:
        raise SettlementInputError("repo must be owner/name")
    number = payload.get("issue_number")
    if type(number) is not int or number <= 0:
        raise SettlementInputError("issue_number must be a positive integer")
    original = _text(payload.get("original_author"), "original_author", maximum=128)
    if original not in _ORIGINAL_ACTORS:
        raise SettlementInputError("only the two authorized original contributors may be named")
    issue_url = _canonical_url(payload.get("issue_url"), repo, "issue", number)
    pr_url = _canonical_url(payload.get("pr_url"), repo, "pr")
    reward = _amount(payload.get("advertised_usd"))

    # Receipts are user-provided pointers, NOT independently verified approval.
    acceptance = _external_evidence(payload.get("sponsor_acceptance_evidence_url"),
                                    "sponsor_acceptance_evidence_url")
    terms = _external_evidence(payload.get("creator_payment_terms_evidence_url"),
                               "creator_payment_terms_evidence_url")
    transfer = _external_evidence(payload.get("transfer_evidence_url"),
                                  "transfer_evidence_url")
    pending = []
    if not acceptance:
        pending.append("Obtain the sponsor's acceptance of this PR")
    if not terms:
        pending.append("Confirm reward eligibility, amount and payment terms with creator")
    if not transfer:
        pending.append("Obtain a transfer receipt and reconcile it to the actual account")
    else:
        pending.append("Reconcile reported transfer evidence with bank/payment-provider records")

    request = (
        f"I, original contributor @{original}, affirmatively request consideration "
        f"for the advertised USD {reward} reward on {issue_url}, based on my "
        f"contribution {pr_url}. Please confirm acceptance, eligibility, the "
        "creator-funded reward amount, the payment method and settlement date. "
        "This requests payment and does not assert that Opire processes claims "
        "or that any payout has been approved or received."
    )
    return {
        "schema": SCHEMA,
        "platform": "Opire",
        "payment_route": "DIRECT_CREATOR_TO_CONTRIBUTOR",
        "platform_handles_claims_or_payments": False,
        "repo": repo, "issue_number": number,
        "issue_url": issue_url, "pr_url": pr_url,
        "original_author": original, "advertised_usd": reward,
        "sponsor_acceptance_evidence_url": acceptance,
        "creator_payment_terms_evidence_url": terms,
        "transfer_evidence_url": transfer,
        "evidence_is_independently_verified": False,
        "bank_verified_paid_usd": None,
        "financial_state": "UNVERIFIED_NEVER_COUNT_AS_RECEIVED",
        "pending_work": pending,
        "creator_payment_request_draft": request,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="offline JSON claim source receipt")
    parser.add_argument("--output", type=Path, help="write generated JSON packet here")
    args = parser.parse_args(argv)
    try:
        source = json.loads(args.input.read_text(encoding="utf-8"))
        packet = compile_settlement_packet(source)
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    result = json.dumps(packet, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        print(result, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
