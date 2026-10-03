# SPDX-License-Identifier: MIT
"""Durable attempts for the Discord-to-chain migration command.

The existing migrations table remains its history/current-state view. Attempts
retain the exact request and provider idempotency key before any credit is sent.
"""

import math
import sqlite3
import uuid

from concierge import config, discord_bridge


_SCHEMA = """
CREATE TABLE IF NOT EXISTS migration_attempts (
    attempt_key TEXT PRIMARY KEY,
    discord_user_id TEXT NOT NULL,
    source_wallet TEXT NOT NULL,
    target_wallet TEXT NOT NULL,
    amount_rtc REAL NOT NULL,
    node_url TEXT NOT NULL,
    discord_host TEXT NOT NULL,
    discord_user TEXT NOT NULL,
    discord_database TEXT NOT NULL,
    tx_hash TEXT,
    pending_id INTEGER,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS migration_active_user
ON migration_attempts (discord_user_id) WHERE status != 'completed';
"""

_NEXT = {
    "prepared": {"transfer_unknown", "pending"},
    "transfer_unknown": {"transfer_unknown", "pending"},
    "pending": {"pending", "confirmed", "failed"},
    "confirmed": {"debit_pending"},
    "debit_pending": {"debit_pending", "partial", "completed"},
    "partial": {"debit_pending"},
    "failed": set(),
    "completed": set(),
}


def _connect():
    connection = discord_bridge._init_tracking_db()
    connection.row_factory = sqlite3.Row
    connection.executescript(_SCHEMA)
    return connection


def get_attempt(discord_id):
    """Return the latest retained attempt, keeping unresolved attempts first."""
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT * FROM migration_attempts WHERE discord_user_id = ? "
            "ORDER BY (status != 'completed') DESC, rowid DESC LIMIT 1",
            (str(discord_id),),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        connection.close()


def prepare_migration(discord_id, source_wallet, target_wallet, amount,
                      node_url, *, force=False):
    """Atomically retain a new attempt before the first provider request.

    A forced migration may follow a completed one. It cannot replace an
    unresolved attempt, including a legacy row that has no resumable key.
    """
    if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0.1:
        raise ValueError("Migration amount must be finite and at least 0.1 RTC")
    discord_id = str(discord_id)
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute(
            "SELECT status FROM migrations WHERE discord_user_id = ?",
            (discord_id,),
        ).fetchone()
        if previous is not None and (not force or previous["status"] != "completed"):
            raise ValueError(
                "Migration already recorded; use --resume for its retained "
                "attempt. --force only starts a new migration after completion."
            )
        active = connection.execute(
            "SELECT attempt_key FROM migration_attempts "
            "WHERE discord_user_id = ? AND status != 'completed'",
            (discord_id,),
        ).fetchone()
        if active is not None:
            raise ValueError("Migration has an unresolved attempt; use --resume")
        attempt_key = "concierge-migration:" + uuid.uuid4().hex
        connection.execute(
            "INSERT INTO migration_attempts "
            "(attempt_key, discord_user_id, source_wallet, target_wallet, "
            "amount_rtc, node_url, discord_host, discord_user, discord_database, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared')",
            (attempt_key, discord_id, source_wallet, target_wallet, amount, node_url,
             config.DISCORD_NAS_HOST, config.DISCORD_NAS_USER, config.DISCORD_DB_PATH),
        )
        connection.execute(
            "INSERT INTO migrations (discord_user_id, target_wallet, amount_rtc, "
            "chain_tx_id, status) VALUES (?, ?, ?, NULL, 'prepared') "
            "ON CONFLICT(discord_user_id) DO UPDATE SET "
            "target_wallet=excluded.target_wallet, amount_rtc=excluded.amount_rtc, "
            "chain_tx_id=NULL, status='prepared', created_at=datetime('now')",
            (discord_id, target_wallet, amount),
        )
        row = connection.execute(
            "SELECT * FROM migration_attempts WHERE attempt_key = ?", (attempt_key,),
        ).fetchone()
        connection.commit()
        return dict(row)
    finally:
        connection.close()


def advance_migration(attempt_key, status, *, tx_hash=None, pending_id=None):
    """Persist progress and its history view in one transaction.

    Concurrent resumptions never roll a completed attempt backwards. The
    remote debit uses the same key to make its transaction idempotent too.
    """
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM migration_attempts WHERE attempt_key = ?", (attempt_key,),
        ).fetchone()
        if row is None:
            raise ValueError("Migration attempt was not found")
        if tx_hash is not None and row["tx_hash"] not in (None, tx_hash):
            raise ValueError("Provider changed the retained migration transaction")
        if pending_id is not None and row["pending_id"] not in (None, pending_id):
            raise ValueError("Provider changed the retained migration pending ID")
        if row["status"] == "completed":
            return dict(row)
        # A slower concurrent resume can finish its read after another process
        # has advanced the same key. Preserve that newer durable progress.
        progressed = {
            "transfer_unknown": {"pending", "confirmed", "debit_pending", "partial", "failed"},
            "pending": {"confirmed", "debit_pending", "partial"},
            "confirmed": {"debit_pending", "partial"},
            "partial": {"debit_pending"},
        }
        if row["status"] in progressed.get(status, set()):
            return dict(row)
        if status not in _NEXT.get(row["status"], set()):
            raise ValueError(f"Cannot change migration {row['status']} to {status}")
        connection.execute(
            "UPDATE migration_attempts SET status=?, tx_hash=COALESCE(?, tx_hash), "
            "pending_id=COALESCE(?, pending_id), updated_at=datetime('now') "
            "WHERE attempt_key=?",
            (status, tx_hash, pending_id, attempt_key),
        )
        updated = connection.execute(
            "SELECT * FROM migration_attempts WHERE attempt_key=?", (attempt_key,),
        ).fetchone()
        cursor = connection.execute(
            "UPDATE migrations SET status=?, chain_tx_id=? "
            "WHERE discord_user_id=? AND target_wallet=? AND amount_rtc=?",
            (status, updated["tx_hash"], updated["discord_user_id"],
             updated["target_wallet"], updated["amount_rtc"]),
        )
        if cursor.rowcount != 1:
            raise ValueError("Migration history no longer matches its retained attempt")
        connection.commit()
        return dict(updated)
    finally:
        connection.close()
