# SPDX-License-Identifier: MIT
"""Payout tracker -- check pending transfers and history against a
RustChain node.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import List
from urllib.parse import urlsplit

import requests

from concierge import config


class PayoutLookupError(RuntimeError):
    """Raised when payout status cannot be determined reliably."""


_HISTORY_PAGE_LIMIT = 200
_HISTORY_MAX_OFFSET = 9800
_HISTORY_MAX_RECORDS = _HISTORY_MAX_OFFSET + _HISTORY_PAGE_LIMIT


def _validate_wallet_id(wallet_id: object) -> str:
    """Return one unambiguous wallet identifier or fail before network I/O."""
    if (
        type(wallet_id) is not str
        or not wallet_id
        or wallet_id != wallet_id.strip()
        or any(char.isspace() or not char.isprintable() for char in wallet_id)
    ):
        raise PayoutLookupError("wallet identifier was invalid")
    return wallet_id


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


def _canonical_history_page(
    data, wallet_id: str
) -> tuple[List[dict], int] | None:
    """Validate one canonical RustChain history page, if present."""
    if not isinstance(data, dict) or "transactions" not in data:
        return None

    items = data.get("transactions")
    total = data.get("total")
    response_wallet = data.get("miner_id")
    if (
        data.get("ok") is not True
        or type(response_wallet) is not str
        or response_wallet != wallet_id
        or not isinstance(items, list)
        or any(not isinstance(item, dict) for item in items)
        or type(total) is not int
        or total < len(items)
    ):
        raise PayoutLookupError("history payout response was malformed")
    return items, total


def _history_payload_list(data, wallet_id: str) -> List[dict]:
    """Normalize the canonical RustChain history envelope plus legacy shapes."""
    page = _canonical_history_page(data, wallet_id)
    if page is None:
        return _payload_list(data, "history")
    return page[0]


def _pending_from_history(items: List[dict]) -> List[dict]:
    """Return in-flight transfer rows from one validated history snapshot.

    Current RustChain exposes public pending-transfer state through
    ``/wallet/history``.  Canonical pending-ledger rows are ``transfer_out``
    entries carrying ``status``; confirmed rows omit that field.  Legacy
    history rows may omit ``type`` but still carry a transfer ``status``.

    Unknown or malformed status values fail closed: silently treating a
    future in-flight state as "no pending payouts" is worse than reporting
    status as unavailable.
    """
    pending: list[dict] = []
    for item in items:
        if "status" not in item:
            continue

        status = item["status"]
        if type(status) is not str or status not in {"pending", "confirming", "confirmed", "failed"}:
            raise PayoutLookupError("pending payout state was malformed")

        item_type = item.get("type")
        if item_type is not None and item_type != "transfer_out":
            raise PayoutLookupError("pending payout state was malformed")

        if status in {"pending", "confirming"}:
            pending.append(item)
    return pending


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


def _is_loopback_host(hostname: str) -> bool:
    """Return whether *hostname* is an explicit local loopback authority."""
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _node_request_settings(node_url: str | None) -> tuple[str, bool | str]:
    """Return a canonical base URL and Requests TLS verification authority.

    Remote payout/status reads require HTTPS.  Local loopback HTTP is retained
    for hermetic development.  HTTPS always verifies peer identity: either via
    the platform trust store or an explicitly configured private/self-signed
    CA bundle.
    """
    raw = config.RUSTCHAIN_NODE_URL if node_url is None else node_url
    if (
        type(raw) is not str
        or not raw
        or raw != raw.strip()
        or any(char.isspace() or not char.isprintable() for char in raw)
    ):
        raise PayoutLookupError("RustChain node URL was invalid")

    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
    except ValueError as exc:
        raise PayoutLookupError("RustChain node URL was invalid") from exc

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or hostname is None
        or username is not None
        or password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PayoutLookupError("RustChain node URL was invalid")

    if parsed.scheme != "https" and not _is_loopback_host(hostname):
        raise PayoutLookupError("remote RustChain payout reads require HTTPS")

    verify: bool | str = True
    ca_bundle = config.RUSTCHAIN_CA_BUNDLE
    if ca_bundle:
        if type(ca_bundle) is not str or ca_bundle != ca_bundle.strip():
            raise PayoutLookupError("RustChain CA bundle configuration was invalid")
        try:
            bundle_path = Path(ca_bundle).expanduser()
            bundle_available = bundle_path.is_file()
        except (OSError, RuntimeError, ValueError) as exc:
            raise PayoutLookupError("RustChain CA bundle was unavailable") from exc
        if not bundle_available:
            raise PayoutLookupError("RustChain CA bundle was unavailable")
        verify = str(bundle_path)

    return raw.rstrip("/"), verify


def _check_transfers(
    wallet_id: str,
    endpoint: str,
    payload_key: str,
    node_url: str | None = None,
) -> List[dict]:
    """Fetch one payout endpoint, distinguishing empty data from failure."""
    wallet_id = _validate_wallet_id(wallet_id)
    base, verify = _node_request_settings(node_url)
    url = f"{base}/wallet/{endpoint}"
    try:
        resp = requests.get(
            url,
            params={"miner_id": wallet_id},
            timeout=15,
            verify=verify,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except (TypeError, ValueError) as exc:
            raise PayoutLookupError(
                f"{payload_key} payout response was not valid JSON"
            ) from exc
        if endpoint == "history":
            return _history_payload_list(data, wallet_id)
        return _payload_list(data, payload_key)
    except PayoutLookupError:
        raise
    except (requests.RequestException, OSError) as exc:
        raise PayoutLookupError(
            f"{payload_key} payout request failed"
        ) from exc


def _check_complete_history(
    wallet_id: str, node_url: str | None = None
) -> List[dict]:
    """Read every canonical history page needed for pending-state authority.

    Canonical RustChain history is paginated.  The first read keeps the
    historical request shape for compatibility; if its ``total`` proves that
    more records exist, subsequent reads use bounded ``limit``/``offset``
    pagination.  A changing total, a stalled page, or a history larger than
    the public offset contract fails closed instead of certifying a partial
    snapshot as "no pending transfers".

    Legacy list/``history`` wrapper responses carry no pagination metadata and
    retain their historical single-response behavior.
    """
    wallet_id = _validate_wallet_id(wallet_id)
    base, verify = _node_request_settings(node_url)
    url = f"{base}/wallet/history"

    collected: list[dict] = []
    expected_total: int | None = None
    offset = 0

    while True:
        params: dict[str, object] = {"miner_id": wallet_id}
        if expected_total is not None:
            remaining = expected_total - offset
            if remaining <= 0:
                return collected
            alignment = offset % _HISTORY_PAGE_LIMIT
            page_limit = (
                _HISTORY_PAGE_LIMIT
                if alignment == 0
                else _HISTORY_PAGE_LIMIT - alignment
            )
            params.update(
                {
                    "limit": min(page_limit, remaining),
                    "offset": offset,
                }
            )

        try:
            resp = requests.get(
                url,
                params=params,
                timeout=15,
                verify=verify,
            )
            resp.raise_for_status()
            try:
                data = resp.json()
            except (TypeError, ValueError) as exc:
                raise PayoutLookupError(
                    "history payout response was not valid JSON"
                ) from exc
        except PayoutLookupError:
            raise
        except (requests.RequestException, OSError) as exc:
            raise PayoutLookupError("history payout request failed") from exc

        canonical = _canonical_history_page(data, wallet_id)
        if canonical is None:
            if expected_total is not None or offset != 0:
                raise PayoutLookupError("history payout pagination was malformed")
            return _payload_list(data, "history")

        page, total = canonical
        if expected_total is None:
            expected_total = total
            if expected_total > _HISTORY_MAX_RECORDS:
                raise PayoutLookupError(
                    "history payout pagination exceeds supported range"
                )
        elif total != expected_total:
            raise PayoutLookupError(
                "history payout pagination changed during read"
            )

        if offset + len(page) > expected_total:
            raise PayoutLookupError("history payout pagination was malformed")

        collected.extend(page)
        offset += len(page)
        if offset == expected_total:
            return collected
        if not page or offset > _HISTORY_MAX_OFFSET:
            raise PayoutLookupError("history payout pagination was incomplete")


def check_pending(wallet_id: str, node_url: str | None = None) -> List[dict]:
    """Return pending transfers for *wallet_id*.

    RustChain's public wallet contract exposes pending-ledger rows through
    ``GET {node_url}/wallet/history?miner_id={wallet_id}``; there is no
    public ``/wallet/pending`` contract.  Canonical history is paginated, so
    all pages are read before deriving in-flight transfer state; this prevents
    a first-page-only read from certifying a false empty pending set.  Legacy
    history shapes retain their single-response compatibility path.

    Transport, HTTP, JSON, envelope, pagination, and pending-state failures
    raise :class:`PayoutLookupError`.
    """
    history = _check_complete_history(wallet_id, node_url)
    return _pending_from_history(history)


def check_history(wallet_id: str, node_url: str | None = None) -> List[dict]:
    """Return recent transfer history for *wallet_id*.

    Queries ``GET {node_url}/wallet/history?miner_id={wallet_id}``.
    Current RustChain returns a 200 ``{ok, miner_id, transactions, total}``
    envelope, including for an empty history. Legacy list/``history`` wrapper
    responses remain accepted for older nodes. Transport, HTTP, JSON, and
    payload failures raise :class:`PayoutLookupError`.
    """
    return _check_transfers(wallet_id, "history", "history", node_url)


def format_payout_status(pending: List[dict], history: List[dict]) -> str:
    """Pretty-print payout status for CLI output.

    Parameters
    ----------
    pending : list[dict]
        Items from :func:`check_pending`. Current entries use ``amount`` and
        ``timestamp``; legacy rows may use ``amount_rtc`` / ``created_at``.
    history : list[dict]
        Items from :func:`check_history`. Current history entries use
        ``amount``; legacy entries may use ``amount_rtc``. Transfer entries
        may include ``from`` / ``to`` and ``timestamp``.
    """
    lines: list[str] = []

    # Pending
    lines.append("-- Pending Transfers --")
    if not pending:
        lines.append("  (none)")
    else:
        for item in pending:
            amount = _terminal_text(item.get("amount_rtc", item.get("amount", "?")))
            memo_value = item.get("memo", "")
            memo = "" if memo_value is None else _terminal_text(memo_value)
            ts_value = item.get("created_at", item.get("timestamp", ""))
            ts = "" if ts_value is None else _terminal_text(ts_value)
            recipient_value = item.get("to", item.get("to_addr", ""))
            recipient = (
                "" if recipient_value is None else _terminal_text(recipient_value)
            )
            status_value = item.get("status", "")
            status = "" if status_value is None else _terminal_text(status_value)
            entry = f"  {amount} RTC"
            if recipient:
                entry += f"  -> {recipient}"
            if status:
                entry += f"  [{status}]"
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
            amount = _terminal_text(item.get("amount_rtc", item.get("amount", "?")))
            sender = _terminal_text(item.get("from", "?"))
            recipient = _terminal_text(item.get("to", "?"))
            ts_value = item.get("timestamp", "")
            ts = "" if ts_value is None else _terminal_text(ts_value)
            entry = f"  {amount} RTC  {sender} -> {recipient}"
            if ts:
                entry += f"  ({ts})"
            lines.append(entry)

    return "\n".join(lines)
