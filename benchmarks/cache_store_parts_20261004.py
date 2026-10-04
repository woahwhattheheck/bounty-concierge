#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compare two bounty_cache.py snapshots, using synthetic data and no network.

Usage: python cache_store_parts_benchmark.py BEFORE.py AFTER.py > result.json
Input construction, correctness checks and reads are outside timed stores.
"""
import gc
import hashlib
import importlib.util
import json
import pathlib
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from unittest.mock import patch


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def blob(path):
    data = pathlib.Path(path).read_bytes()
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def run(before_path, after_path):
    before, after = module(before_path, "before"), module(after_path, "after")
    report = {"python": sys.version, "platform": platform.platform(),
              "before_blob": blob(before_path), "after_blob": blob(after_path),
              "checks": [], "benchmarks": [], "provider_requests": 0}
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        repo, token = "synthetic/example", "NOT_A_PROVIDER_CREDENTIAL"
        caches = [m.PageCache(root / str(i), token) for i, m in enumerate((before, after))]
        key = caches[0]._key(repo, 1)
        paths = [cache.root / (key + ".json") for cache in caches]

        # Compare accepted bytes and cross-read them with both source versions.
        normal = [{"title": "empty/false/zero", "body": "", "n": 0, "flag": False}]
        unicode = [{"title": "escape\"\n", "body": "\U0001f9ed" * 8000} for _ in range(100)]
        large = [{"title": f"Issue {i}", "body": str(i) + "a" * 68000} for i in range(100)]
        for name, issues, etag in [("ordinary-empty-etag", normal, '""'),
                                    ("unicode-weak-etag", unicode, 'W/"same"'),
                                    ("large-accepted", large, '"large"')]:
            # Unicode is 9.6 MB escaped: use a bounded accepted variant.
            if name == "unicode-weak-etag":
                issues = [{**issue, "body": issue["body"][:4000]} for issue in issues]
            for cache in caches:
                assert cache.store(repo, 1, etag, issues, True) == "stored", name
            raw = [path.read_bytes() for path in paths]
            assert raw[0] == raw[1], name
            for m in (before, after):
                for cache in caches:
                    entry, error = m.PageCache(cache.root, token).load(repo, 1)
                    assert not error and entry["issues"] == issues and entry["has_next"] is True, name
            assert all(path.stat().st_mode & 0o777 == 0o600 for path in paths), name
            report["checks"].append({"name": name, "status": "pass", "identical_bytes": len(raw[0])})

        # Exercise the exact serialized-envelope bound, not an estimate.
        empty = {"key": key, "etag": '"bound"', "issues": [{"body": ""}], "has_next": False}
        overhead = len(before._bytes({"entry": empty, "sha256": "0" * 64}))
        boundary = [{"body": "a" * (before._MAX_BYTES - overhead)}]
        for cache, path in zip(caches, paths):
            assert cache.store(repo, 1, '"bound"', boundary, False) == "stored"
            assert path.stat().st_size == before._MAX_BYTES
            saved = path.read_bytes()
            assert cache.store(repo, 1, '"bound"', [{"body": boundary[0]["body"] + "a"}], False) == "skipped"
            assert path.read_bytes() == saved
        report["checks"].append({"name": "exact-bound-and-one-byte-over", "status": "pass"})

        # Oversized valid and invalid tails retain status and do not touch disk.
        oversized = [{"body": "a" * 100000} for _ in range(100)]
        bad_tail = oversized[:-1] + [{"body": float("nan")}]
        for name, issues, expected in [("oversized", oversized, "skipped"), ("invalid-tail", bad_tail, "error")]:
            for i, m in enumerate((before, after)):
                absent = root / f"absent-{name}-{i}"
                assert m.PageCache(absent, token).store(repo, 1, '"same"', issues, False) == expected
                assert not absent.exists()
                saved = paths[i].read_bytes()
                assert caches[i].store(repo, 1, '"same"', issues, False) == expected
                assert paths[i].read_bytes() == saved
            report["checks"].append({"name": name, "status": "pass", "result": expected})

        # A failed final replace leaves the prior entry and no temporary file.
        for cache, path in zip(caches, paths):
            saved = path.read_bytes()
            with patch("os.replace", side_effect=OSError("synthetic replace failure")):
                assert cache.store(repo, 1, '"new"', normal, False) == "error"
            assert path.read_bytes() == saved
            assert not list(cache.root.glob(".bounty-page-*"))
        report["checks"].append({"name": "failed-replace-preserves-entry", "status": "pass"})

        # Alternate order; fsync and atomic replace are included in each store.
        small = [{"title": f"Issue {i}", "body": "a" * 320} for i in range(100)]
        for name, issues in [("small-page", small), ("large-page", large)]:
            timings, peaks = [[], []], [[], []]
            for cache in caches:
                assert cache.store(repo, 1, '"bench"', issues, False) == "stored"
            for repeat in range(9):
                for i in ((0, 1) if repeat % 2 == 0 else (1, 0)):
                    start = time.perf_counter_ns()
                    status = caches[i].store(repo, 1, '"bench"', issues, False)
                    timings[i].append((time.perf_counter_ns() - start) / 1e6)
                    assert status == "stored"
            for repeat in range(3):
                for i in ((0, 1) if repeat % 2 == 0 else (1, 0)):
                    gc.collect()
                    tracemalloc.start()
                    assert caches[i].store(repo, 1, '"bench"', issues, False) == "stored"
                    peaks[i].append(tracemalloc.get_traced_memory()[1])
                    tracemalloc.stop()
            assert paths[0].read_bytes() == paths[1].read_bytes()
            report["benchmarks"].append({"name": name, "serialized_bytes": paths[0].stat().st_size,
                                        "before_ms": timings[0], "after_ms": timings[1],
                                        "before_median_ms": statistics.median(timings[0]),
                                        "after_median_ms": statistics.median(timings[1]),
                                        "before_peak_bytes": peaks[0], "after_peak_bytes": peaks[1],
                                        "before_median_peak_bytes": statistics.median(peaks[0]),
                                        "after_median_peak_bytes": statistics.median(peaks[1])})
    return report


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print(json.dumps(run(sys.argv[1], sys.argv[2]), indent=2))
