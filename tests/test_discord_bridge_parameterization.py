# SPDX-License-Identifier: MIT
"""Hermetic tests for Discord migration bridge parameter handling."""

import contextlib
import io
import pathlib
import sqlite3
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge import discord_bridge


def _local_script_runner(script):
    """Execute the generated NAS Python script locally with no SSH/network."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    returncode = 0
    namespace = {}
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            exec(compile(script, "<discord-bridge-test>", "exec"), namespace, namespace)
        except SystemExit as exc:
            returncode = int(exc.code or 0)
        except Exception as exc:  # Mirror a failed remote Python process.
            print(str(exc), file=sys.stderr)
            returncode = 1
    return stdout.getvalue().strip(), stderr.getvalue().strip(), returncode


@pytest.fixture()
def economy_db(tmp_path, monkeypatch):
    db_path = tmp_path / "economy.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE balances (
            user_id TEXT PRIMARY KEY,
            balance REAL NOT NULL,
            total_earned REAL NOT NULL,
            total_spent REAL NOT NULL
        );
        CREATE TABLE transactions (
            from_user TEXT NOT NULL,
            to_user TEXT NOT NULL,
            amount REAL NOT NULL,
            type TEXT NOT NULL,
            description TEXT NOT NULL
        );
        """
    )
    con.executemany(
        "INSERT INTO balances (user_id, balance, total_earned, total_spent) "
        "VALUES (?, ?, ?, ?)",
        [
            ("alice", 10.0, 12.0, 2.0),
            ("bob", 20.0, 20.0, 0.0),
        ],
    )
    con.commit()
    con.close()

    monkeypatch.setattr(discord_bridge.config, "DISCORD_DB_PATH", str(db_path))
    monkeypatch.setattr(discord_bridge, "_ssh_run_script", _local_script_runner)
    return db_path


@pytest.fixture()
def tracking_db(tmp_path, monkeypatch):
    tracking_dir = tmp_path / "tracking"
    tracking_path = tracking_dir / "migrations.db"
    monkeypatch.setattr(discord_bridge, "_TRACKING_DIR", str(tracking_dir))
    monkeypatch.setattr(discord_bridge, "_TRACKING_DB", str(tracking_path))
    con = discord_bridge._init_tracking_db()
    con.close()
    return tracking_path


def _balance(db_path, user_id):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "SELECT balance, total_spent FROM balances WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    finally:
        con.close()


def _transactions(db_path):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "SELECT from_user, to_user, amount, type FROM transactions ORDER BY rowid"
        ).fetchall()
    finally:
        con.close()


def test_balance_lookup_treats_hostile_user_id_as_data(economy_db):
    hostile = "missing' OR 1=1 --"

    result = discord_bridge.get_discord_balance(hostile)

    assert "error" in result
    assert "not found" in result["error"]
    assert discord_bridge.get_discord_balance("bob")["balance"] == 20.0


@pytest.mark.parametrize("status", ["partial", "pending", "unknown"])
def test_unresolved_migration_blocks_balance_preflight_before_remote_query(
    economy_db, tracking_db, monkeypatch, status
):
    assert discord_bridge.record_migration(
        "alice", "alice-chain", 10.0, "pending-123", status
    )
    remote_called = False

    def forbidden_runner(script):
        nonlocal remote_called
        remote_called = True
        raise AssertionError("unresolved migration must not query remote balance")

    monkeypatch.setattr(discord_bridge, "_ssh_run_script", forbidden_runner)

    result = discord_bridge.get_discord_balance("alice")

    assert "error" in result
    assert "unresolved migration state" in result["error"]
    assert status in result["error"]
    assert remote_called is False
    assert _balance(economy_db, "alice") == (10.0, 2.0)


def test_completed_migration_preserves_explicit_force_preflight_semantics(
    economy_db, tracking_db
):
    assert discord_bridge.record_migration(
        "alice", "alice-chain", 10.0, "settled-123", "completed"
    )

    result = discord_bridge.get_discord_balance("alice")

    assert result["user_id"] == "alice"
    assert result["balance"] == 10.0


def test_holder_threshold_rejects_non_numeric_sql_payload(economy_db):
    result = discord_bridge.list_discord_holders("0 OR 1=1")

    assert result == {"error": "Minimum balance must be a finite number"}
    assert [row["user_id"] for row in discord_bridge.list_discord_holders("10")] == [
        "bob",
        "alice",
    ]


def test_debit_is_parameterized_atomic_and_records_one_transaction(economy_db):
    result = discord_bridge.debit_discord_balance("bob", 5)

    assert result is True
    assert _balance(economy_db, "bob") == (15.0, 5.0)
    assert _transactions(economy_db) == [
        ("bob", "CHAIN_MIGRATION", 5.0, "migration"),
    ]


def test_debit_hostile_user_id_cannot_select_or_debit_another_row(economy_db):
    hostile = "missing' OR 1=1 --"

    result = discord_bridge.debit_discord_balance(hostile, 5)

    assert "source balance row missing or insufficient" in result["error"]
    assert _balance(economy_db, "alice") == (10.0, 2.0)
    assert _balance(economy_db, "bob") == (20.0, 0.0)
    assert _transactions(economy_db) == []


def test_debit_insufficient_balance_rolls_back_without_transaction(economy_db):
    result = discord_bridge.debit_discord_balance("alice", 50)

    assert "source balance row missing or insufficient" in result["error"]
    assert _balance(economy_db, "alice") == (10.0, 2.0)
    assert _transactions(economy_db) == []


@pytest.mark.parametrize(
    "amount",
    [
        -1,
        0,
        "NaN",
        "Infinity",
        True,
        "__import__('os').system('echo injected') or 1",
    ],
)
def test_debit_rejects_invalid_amount_before_remote_script(economy_db, monkeypatch, amount):
    called = False

    def forbidden_runner(script):
        nonlocal called
        called = True
        raise AssertionError("invalid debit must not execute a remote script")

    monkeypatch.setattr(discord_bridge, "_ssh_run_script", forbidden_runner)

    result = discord_bridge.debit_discord_balance("alice", amount)

    assert "error" in result
    assert called is False
    assert _balance(economy_db, "alice") == (10.0, 2.0)
    assert _transactions(economy_db) == []
