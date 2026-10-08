# SPDX-License-Identifier: MIT
"""Deterministic acceptance-velocity decision; no claim/payout authority."""
from __future__ import annotations
import re
from typing import Any
from concierge._sponsor_liquidity_input import (
    RECEIPT_SCHEMA, SponsorLiquidityInputError, digest, normalize,
)

AUTHORITY = {
    "advisory_only": True, "new_build_authority": False,
    "existing_work_cancellation_authority": False,
    "claim_or_submission_authority": False, "payment_or_collection_authority": False,
}


def compile_liquidity_gate(request: dict[str, Any]) -> dict[str, Any]:
    """Hold new builds when existing original-actor PRs accumulate unaccepted."""
    r, age = normalize(request)
    actor = r["actor_login"]
    mine_open = sum(p["author_login"] == actor for p in r["open_prs"])
    mine_merged = sum(p["author_login"] == actor and p["merged_at"] is not None
                      for p in r["closed_prs"])
    mine_rejected = sum(p["author_login"] == actor and p["merged_at"] is None
                        for p in r["closed_prs"])
    all_merged = sum(p["merged_at"] is not None for p in r["closed_prs"])
    reasons = []
    if age > r["max_age_seconds"]:
        reasons.append("OBSERVATION_STALE")
    if not r["all_open_prs"] or not r["all_closed_prs_since_window"]:
        reasons.append("INVENTORY_INCOMPLETE")
    if mine_open >= 8 and mine_merged == 0:
        reasons.append("EIGHT_PLUS_OPEN_WITH_ZERO_ACCEPTED")
    if mine_rejected >= 5 and mine_merged == 0:
        reasons.append("FIVE_PLUS_CLOSED_UNMERGED_WITH_ZERO_ACCEPTED")
    if mine_open >= 24 and mine_merged <= 1:
        reasons.append("TWENTY_FOUR_PLUS_OPEN_WITH_AT_MOST_ONE_ACCEPTED")
    decision = ("HOLD_EVIDENCE" if any(x in reasons for x in ("OBSERVATION_STALE", "INVENTORY_INCOMPLETE"))
                else "HOLD_NEW_BUILD" if reasons else "REVIEW_OTHER_GATES")
    body = {"schema": RECEIPT_SCHEMA, "input": r,
            "disposition": decision, "reason_codes": reasons,
            "counts": {"actor_open": mine_open,
                       "actor_merged_in_window": mine_merged,
                       "actor_closed_unmerged_in_window": mine_rejected,
                       "all_open": len(r["open_prs"]),
                       "all_merged_in_window": all_merged},
            "snapshot_age_seconds": age, "authority": AUTHORITY,
            "provider_authentication": "CALLER_SUPPLIED_NOT_AUTHENTICATED"}
    return {**body, "receipt_sha256": digest(body)}


def verify_receipt(receipt: Any) -> bool:
    if type(receipt) is not dict or receipt.get("schema") != RECEIPT_SCHEMA:
        return False
    try:
        checksum = receipt.get("receipt_sha256")
        return (type(checksum) is str and re.fullmatch(r"[0-9a-f]{64}", checksum) is not None
                and digest({k: v for k, v in receipt.items() if k != "receipt_sha256"}) == checksum
                and compile_liquidity_gate(receipt["input"]) == receipt)
    except (SponsorLiquidityInputError, KeyError, TypeError, ValueError, OverflowError):
        return False
