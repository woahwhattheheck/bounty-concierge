# SPDX-License-Identifier: MIT
"""Focused tests for shared GitHub read coalescing."""
import sqlite3

import pytest

from concierge.github_read_coalesce import GitHubReadCoalescer, coalesced_read


def test_single_flight(tmp_path):
    cache = GitHubReadCoalescer(tmp_path / "reads.sqlite", scope="managed-app:actor-293")
    first = cache.admit("repo/example#pr:7", "worker-a", now_epoch=1000)
    second = cache.admit("repo/example#pr:7", "worker-b", now_epoch=1001)

    assert first.status == "FRESH"
    assert second.status == "IN_FLIGHT"
    assert second.retry_after_seconds == 4


def test_short_ttl_cache_then_stale_refresh(tmp_path):
    cache = GitHubReadCoalescer(tmp_path / "reads.sqlite", scope="managed-app:actor-293")
    first = cache.admit("repo/example#issue:9", "worker-a", now_epoch=1000)
    assert cache.publish(
        "repo/example#issue:9",
        "worker-a",
        first.generation,
        {"state": "open"},
        now_epoch=1000,
    )

    cached = cache.admit("repo/example#issue:9", "worker-b", now_epoch=1002)
    stale = cache.admit("repo/example#issue:9", "worker-c", now_epoch=1003.1)

    assert cached.status == "CACHED"
    assert cached.payload == {"state": "open"}
    assert cached.advisory_only is True
    assert stale.status == "STALE"
    assert stale.payload is None


def test_generation_blocks_late_owner(tmp_path):
    cache = GitHubReadCoalescer(
        tmp_path / "reads.sqlite", scope="managed-app", lease_seconds=2
    )
    first = cache.admit("repo/example#pr:10", "worker-a", now_epoch=1000)
    second = cache.admit("repo/example#pr:10", "worker-b", now_epoch=1003)

    assert second.generation == first.generation + 1
    assert not cache.publish(
        "repo/example#pr:10",
        "worker-a",
        first.generation,
        {"head": "old"},
        now_epoch=1003,
    )
    assert cache.publish(
        "repo/example#pr:10",
        "worker-b",
        second.generation,
        {"head": "new"},
        now_epoch=1003,
    )


def test_coalesced_read_calls_provider_once(tmp_path):
    cache = GitHubReadCoalescer(tmp_path / "reads.sqlite", scope="managed-app")
    calls = {"count": 0}

    def reader():
        calls["count"] += 1
        return {"head": "abc"}

    first = coalesced_read(
        cache, "repo/example#pr:11", "worker-a", reader, now_epoch=1000
    )
    second = coalesced_read(
        cache, "repo/example#pr:11", "worker-b", reader, now_epoch=1001
    )

    assert calls["count"] == 1
    assert first.provider_called is True
    assert second.status == "CACHED"
    assert second.provider_called is False


def test_failed_reader_releases_lease(tmp_path):
    cache = GitHubReadCoalescer(tmp_path / "reads.sqlite", scope="managed-app")

    def failed():
        raise RuntimeError("read failed")

    with pytest.raises(RuntimeError):
        coalesced_read(
            cache, "repo/example#issue:12", "worker-a", failed, now_epoch=1000
        )

    assert (
        cache.admit("repo/example#issue:12", "worker-b", now_epoch=1000).status
        == "FRESH"
    )


def test_store_hashes_scope_query_and_owner(tmp_path):
    path = tmp_path / "reads.sqlite"
    cache = GitHubReadCoalescer(path, scope="managed-app:actor-293")
    first = cache.admit("repo/private#pr:13", "worker-a", now_epoch=1000)
    assert cache.publish(
        "repo/private#pr:13",
        "worker-a",
        first.generation,
        {"state": "open"},
        now_epoch=1000,
    )

    connection = sqlite3.connect(path)
    try:
        scope_key, query_key, owner = connection.execute(
            "SELECT scope_key, query_key, lease_owner FROM github_read_coalesce_v1"
        ).fetchone()
    finally:
        connection.close()

    assert scope_key != "managed-app:actor-293"
    assert query_key != "repo/private#pr:13"
    assert owner is None
    raw = path.read_bytes()
    assert b"managed-app:actor-293" not in raw
    assert b"repo/private#pr:13" not in raw
    assert b"worker-a" not in raw
