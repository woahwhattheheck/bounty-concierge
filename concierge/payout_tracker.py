# SPDX-License-Identifier: MIT
"""Payout tracker -- check pending transfers and history against a
RustChain node.
"""

from __future__ import annotations

from typing import List

import requests

from concierge import config


def _payload_list(data, key: str) -> List[dict]:
    """Normalize supported API payload shapes to the public list contract."""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get(key, [])
    else:
        return []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


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


def check_pending(wallet_id: str, node_url: str | None = None) -> List[dict]:
    """Return pending transfers for *wallet_id*.

    Queries ``GET {node_url}/wallet/pending?miner_id={wallet_id}``.
    Returns an empty list on 404, connection errors, or unsupported payloads.
    """
    base = (node_url or config.RUSTCHAIN_NODE_URL).rstrip("/")
    url = f"{base}/wallet/pending"
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
        return _payload_list(resp.json(), "pending")
    except requests.RequestException:
        return []


def check_history(wallet_id: str, node_url: str | None = None) -> List[dict]:
    """Return recent transfer history for *wallet_id*.

    Queries ``GET {node_url}/wallet/history?miner_id={wallet_id}``.
    Returns an empty list on 404, connection errors, or unsupported payloads.
    """
    base = (node_url or config.RUSTCHAIN_NODE_URL).rstrip("/")
    url = f"{base}/wallet/history"
    try:
        resp = requests.get(
            url,
            params={"miner_id": wallet_id},
            timeout=15,
            verify=False,
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return _payload_list(resp.json(), "history")
    except requests.RequestException:
        return []


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
