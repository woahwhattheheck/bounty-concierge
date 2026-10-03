# SPDX-License-Identifier: MIT
"""Resume one retained wallet migration without issuing a second credit."""

import math
import re

from concierge import config, discord_bridge, wallet_helper
from concierge.migration_journal import advance_migration, get_attempt


def _acknowledged_transfer(response, attempt):
    """Bind the current node's acknowledgement to the retained request."""
    if (not isinstance(response, dict) or response.get("ok") is not True
            or "error" in response):
        return False
    amount = response.get("amount_rtc")
    return (
        response.get("from_miner") == attempt["source_wallet"]
        and response.get("to_miner") == attempt["target_wallet"]
        and type(amount) in (int, float)
        and math.isfinite(amount)
        and amount == attempt["amount_rtc"]
        and type(response.get("pending_id")) is int
        and response["pending_id"] > 0
        and isinstance(response.get("tx_hash"), str)
        and re.fullmatch(r"[0-9a-f]{32}", response["tx_hash"]) is not None
        and type(response.get("phase")) is str
        and response["phase"] in {"pending", "confirming", "confirmed"}
    )


def continue_migration(attempt):
    """Return (retained attempt, explanation, exit code) after useful progress.

    Exit 2 means the credit is still pending. There is no polling, automatic
    resubmission with a new key, or debit before a confirmed status lookup.
    """
    retained = get_attempt(attempt["discord_user_id"])
    if retained is None or retained["attempt_key"] != attempt["attempt_key"]:
        raise ValueError("Migration attempt is no longer the current retained attempt")
    attempt = retained
    key = attempt["attempt_key"]
    if attempt["status"] == "completed":
        return attempt, "Migration already completed; no provider action performed.", 0
    if attempt["node_url"] != config.RUSTCHAIN_NODE_URL:
        raise ValueError("Use the retained RustChain node URL to resume this migration")
    if (attempt["discord_host"], attempt["discord_user"], attempt["discord_database"]) != (
        config.DISCORD_NAS_HOST, config.DISCORD_NAS_USER, config.DISCORD_DB_PATH,
    ):
        raise ValueError("Use the retained Discord database configuration to resume this migration")
    if attempt["status"] == "failed":
        return attempt, "The retained transfer failed; no Discord debit performed.", 1

    if attempt["status"] in {"prepared", "transfer_unknown"}:
        response = wallet_helper.transfer_rtc(
            attempt["source_wallet"], attempt["target_wallet"], attempt["amount_rtc"],
            reason="Discord economy migration " + key,
            idempotency_key=key,
        )
        if not _acknowledged_transfer(response, attempt):
            attempt = advance_migration(key, "transfer_unknown")
            return attempt, (
                "Transfer was not acknowledged with a matching receipt; no Discord "
                "debit performed. Resume this attempt to reuse its idempotency key."
            ), 1
        attempt = advance_migration(
            key, "pending", tx_hash=response["tx_hash"], pending_id=response["pending_id"],
        )

    if attempt["status"] == "pending":
        response = wallet_helper.get_transfer_status(attempt["tx_hash"])
        if (not isinstance(response, dict) or response.get("ok") is not True
                or "error" in response
                or response.get("tx_hash") != attempt["tx_hash"]):
            return attempt, "Transfer status unavailable; no Discord debit performed.", 1
        status = response.get("status")
        if type(status) is not str:
            return attempt, "Transfer status was malformed; no Discord debit performed.", 1
        if status == "failed":
            attempt = advance_migration(key, "failed")
            return attempt, "Transfer failed; no Discord debit performed.", 1
        confirmations = response.get("confirmations")
        if status in {"pending", "confirming"}:
            return attempt, "Transfer is pending; resume after confirmation.", 2
        if (status != "confirmed" or type(confirmations) is not int
                or confirmations < 1):
            return attempt, "Transfer confirmation was not established; no Discord debit performed.", 1
        attempt = advance_migration(key, "confirmed")

    if attempt["status"] in {"confirmed", "debit_pending", "partial"}:
        attempt = advance_migration(key, "debit_pending")
        if attempt["status"] == "completed":
            return attempt, "Migration already completed; no further debit performed.", 0
        result = discord_bridge.debit_discord_balance(
            attempt["discord_user_id"], attempt["amount_rtc"], migration_key=key,
        )
        if result is not True:
            attempt = advance_migration(key, "partial")
            if attempt["status"] == "completed":
                return attempt, "Migration already completed by another resumed process.", 0
            return attempt, (
                "Credit is confirmed; Discord debit is unresolved. Resume this "
                "attempt to reconcile the same debit without another credit."
            ), 1
        attempt = advance_migration(key, "completed")

    if attempt["status"] != "completed":
        raise ValueError("Migration has an unsupported retained state")
    return attempt, "Confirmed credit and Discord debit recorded; migration complete.", 0
