# SPDX-License-Identifier: MIT
"""Payout tracker -- check pending transfers and history against a
RustChain node.
"""

from __future__ import annotations

from typing import List

import requests

from concierge import config


def _record_list(payload: object, key: str) -> List[dict]:
    """Return a validated record list from a direct or wrapped API payload.

    The node endpoints historically return either a bare list of records or an
    object containing ``pending``/``history``.  Successful HTTP status is not
    enough to trust the JSON shape: returning a scalar, ``null``, or a mixed
    list would otherwise violate this module's ``List[dict]`` contract and can
    crash the CLI formatter later.  Malformed shapes therefore fail closed to
    an empty list, matching the existing network/HTTP-error behavior.
    """
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = payload.get(key, [])
    else:
        return []

    if not isinstance(records, list):
        return []
    if not all(isinstance(item, dict) for item in records):
        return []
    return records


def check_pending(wallet_id: str, node_url: str | None = None) -> List[dict]:
    """Return pending transfers for *wallet_id*.

    Queries ``GET {node_url}/wallet/pending?miner_id={wallet_id}``.
    Returns an empty list on 404, connection errors, or malformed payloads.
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
        return _record_list(resp.json(), "pending")
    except requests.RequestException:
        return []


def check_history(wallet_id: str, node_url: str | None = None) -> List[dict]:
    """Return recent transfer history for *wallet_id*.

    Queries ``GET {node_url}/wallet/history?miner_id={wallet_id}``.
    Returns an empty list on 404, connection errors, or malformed payloads.
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
        return _record_list(resp.json(), "history")
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
            amount = item.get("amount_rtc", "?")
            memo = item.get("memo", "")
            ts = item.get("created_at", "")
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
            amount = item.get("amount_rtc", "?")
            sender = item.get("from", "?")
            recipient = item.get("to", "?")
            ts = item.get("timestamp", "")
            entry = f"  {amount} RTC  {sender} -> {recipient}"
            if ts:
                entry += f"  ({ts})"
            lines.append(entry)

    return "\n".join(lines)
