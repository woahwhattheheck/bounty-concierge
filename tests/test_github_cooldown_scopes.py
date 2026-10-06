# SPDX-License-Identifier: MIT
"""Focused compatibility checks for route-scoped GitHub cooldowns."""

from concierge.github_cooldown import GitHubCooldown


def test_provider_cooldowns_can_be_isolated_while_quota_stays_global(tmp_path):
    path = tmp_path / "cooldown.sqlite"
    legacy = GitHubCooldown(path, "test-credential")
    search = GitHubCooldown(path, "test-credential", cooldown_scope="search")
    core = GitHubCooldown(path, "test-credential", cooldown_scope="core")

    search.extend(1200.0)

    assert search.deadline() == 1200.0
    assert core.deadline() is None
    assert legacy.deadline() is None

    search.reserve_quota_until(1500.0)

    assert search.quota_reserve_deadline() == 1500.0
    assert core.quota_reserve_deadline() == 1500.0
    assert legacy.quota_reserve_deadline() == 1500.0


def test_default_scope_preserves_legacy_row_identity(tmp_path):
    path = tmp_path / "cooldown.sqlite"
    first = GitHubCooldown(path, "same-credential")
    first.extend(1300.0)

    reopened = GitHubCooldown(path, "same-credential")

    assert reopened.deadline() == 1300.0
    assert reopened.scope == first.scope
    assert reopened.cooldown_scope == first.scope


def test_scoped_unknown_secondary_backoff_is_independent(tmp_path, monkeypatch):
    path = tmp_path / "cooldown.sqlite"
    from concierge import github_cooldown

    clock = [1000.0]
    monkeypatch.setattr(github_cooldown, "time", lambda: clock[0])

    search = GitHubCooldown(path, "test-credential", cooldown_scope="search")
    core = GitHubCooldown(path, "test-credential", cooldown_scope="core")

    assert search.extend_unknown_secondary() == 1060.0
    assert core.deadline() is None

    clock[0] = 1060.0
    assert search.extend_unknown_secondary() == 1180.0
    assert core.extend_unknown_secondary() == 1120.0

def test_schema_ddl_runs_once_per_helper_instance(tmp_path, monkeypatch):
    path = tmp_path / "cooldown.sqlite"
    from concierge import github_cooldown

    statements: list[str] = []
    real_connect = github_cooldown.sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(github_cooldown.sqlite3, "connect", traced_connect)

    cooldown = GitHubCooldown(path, "test-credential")
    assert cooldown.deadline() is None
    cooldown.extend(1200.0)
    assert cooldown.quota_reserve_deadline() is None

    schema_statements = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("CREATE TABLE")
    ]
    assert len(schema_statements) == 4

def test_remaining_wait_seconds_uses_longest_shared_deadline(tmp_path, monkeypatch):
    path = tmp_path / "cooldown.sqlite"
    from concierge import github_cooldown

    clock = [1000.25]
    monkeypatch.setattr(github_cooldown, "time", lambda: clock[0])

    cooldown = GitHubCooldown(path, "test-credential")
    assert cooldown.remaining_wait_seconds() == 0

    cooldown.extend(1060.0)
    assert cooldown.remaining_wait_seconds() == 60

    cooldown.reserve_quota_until(1090.1)
    assert cooldown.remaining_wait_seconds() == 90

    clock[0] = 1090.1
    assert cooldown.remaining_wait_seconds() == 0

def test_unknown_secondary_samples_clock_after_write_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from concierge import github_cooldown

    clock = [1000.0]
    monkeypatch.setattr(github_cooldown, "time", lambda: clock[0])
    cooldown = GitHubCooldown(tmp_path / "cooldown.sqlite", "test-credential")

    class Result:
        def __init__(self, row=None):
            self.row = row
        def fetchone(self):
            return self.row

    class Connection:
        observed_epoch = None
        deadline = None

        def execute(self, sql, params=()):
            if sql == "BEGIN IMMEDIATE":
                clock[0] = 1060.0
                return Result()
            if sql.startswith("SELECT backoff_seconds"):
                return Result(None)
            if sql.startswith("INSERT INTO github_cooldown_unknown_v1"):
                self.observed_epoch = params[2]
                return Result()
            if sql.startswith("INSERT INTO github_cooldown_v1"):
                self.deadline = params[1]
                return Result()
            if sql.startswith("SELECT until_epoch"):
                return Result((self.deadline,))
            raise AssertionError(f"unexpected SQL: {sql}")

    connection = Connection()

    @contextmanager
    def fake_connection():
        yield connection

    monkeypatch.setattr(cooldown, "_connection", fake_connection)

    assert cooldown.extend_unknown_secondary() == 1120.0
    assert connection.observed_epoch == 1060.0

