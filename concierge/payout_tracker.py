# SPDX-License-Identifier: MIT
"""Payout tracker -- check pending transfers and history against a
RustChain node.
"""

from __future__ import annotations

from typing import List

import requests

from concierge import config


class PayoutLookupError(RuntimeError):
    """Raised when payout status cannot be determined reliably."""


def _payload_list(data, key: str) -> List[dict]:
    """Normalize supported API payload shapes without certifying bad data."""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and key in data:
        items = data[key]
    else:
        raise PayoutLookupError(f"{key} payout response was malformed")

    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise PayoutLookupError(f"{key} payout response was malformed")
    return items


def _terminal_text(value) -> str:
    """Render an untrusted field without raw terminal-control characters."""
    text = str(value)
    escaped: list[str] = []
    for char in text:
        if char.isprintable():
            escaped.append(char)
            continue
        codepoint = ord(char)
        if codepoint <= 0xFF:
            escaped.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            escaped.append(f"\\u{codepoint:04x}")
        else:
            escaped.append(f"\\U{codepoint:08x}")
    return "".join(escaped)


def _check_transfers(
    wallet_id: str,
    endpoint: str,
    payload_key: str,
    node_url: str | None = None,
) -> List[dict]:
    """Fetch one payout endpoint, distinguishing empty data from failure."""
    base = (node_url or config.RUSTCHAIN_NODE_URL).rstrip("/")
    url = f"{base}/wallet/{endpoint}"
    try:
        resp = requests.get(
            url,
            params={"miner_id": wallet_id},
            timeout=15,
            verify=False,  # self-signed cert on node
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        try:
            data = resp.json()
        except (TypeError, ValueError) as exc:
            raise PayoutLookupError(
                f"{payload_key} payout response was not valid JSON"
            ) from exc
        return _payload_list(data, payload_key)
    except PayoutLookupError:
        raise
    except requests.RequestException as exc:
        raise PayoutLookupError(
            f"{payload_key} payout request failed"
        ) from exc


def check_pending(wallet_id: str, node_url: str | None = None) -> List[dict]:
    """Return pending transfers for *wallet_id*.

    Queries ``GET {node_url}/wallet/pending?miner_id={wallet_id}``.
    A genuine 404 remains an empty result. Transport, HTTP, JSON, and payload
    failures raise :class:`PayoutLookupError` rather than masquerading as no
    pending transfers.
    """
    return _check_transfers(wallet_id, "pending", "pending", node_url)


def check_history(wallet_id: str, node_url: str | None = None) -> List[dict]:
    """Return recent transfer history for *wallet_id*.

    Queries ``GET {node_url}/wallet/history?miner_id={wallet_id}``.
    A genuine 404 remains an empty result. Transport, HTTP, JSON, and payload
    failures raise :class:`PayoutLookupError` rather than masquerading as no
    transfer history.
    """
    return _check_transfers(wallet_id, "history", "history", node_url)


def format_payout_status(pending: List[dict], history: List[dict]) -> str:
    """Pretty-print payout status for CLI output.

    Parameters
    ----------
    pending : list[dict]
        Items from :func:`check_pending`.  Each dict should have at least
        ``amount_rtc`` and optionally ``memo``, ``created_at``.
    history : list[dict]
        Items from :func:`check_history`.  Each dict should have at least
        ``amount_rtc``, ``from``, ``to``, and optionally ``timestamp``.
    """
    lines: list[str] = []

    # Pending
    lines.append("-- Pending Transfers --")
    if not pending:
        lines.append("  (none)")
    else:
        for item in pending:
            amount = _terminal_text(item.get("amount_rtc", "?"))
            memo_value = item.get("memo", "")
            memo = "" if memo_value is None else _terminal_text(memo_value)
            ts_value = item.get("created_at", "")
            ts = "" if ts_value is None else _terminal_text(ts_value)
            entry = f"  {amount} RTC"
            if memo:
                entry += f"  memo: {memo}"
            if ts:
                entry += f"  ({ts})"
            lines.append(entry)

    lines.append("")

    # History
    lines.append("-- Recent History --")
    if not history:
        lines.append("  (none)")
    else:
        for item in history:
            amount = _terminal_text(item.get("amount_rtc", "?"))
            sender = _terminal_text(item.get("from", "?"))
            recipient = _terminal_text(item.get("to", "?"))
            ts_value = item.get("timestamp", "")
            ts = "" if ts_value is None else _terminal_text(ts_value)
            entry = f"  {amount} RTC  {sender} -> {recipient}"
            if ts:
                entry += f"  ({ts})"
            lines.append(entry)

    return "\n".join(lines)
