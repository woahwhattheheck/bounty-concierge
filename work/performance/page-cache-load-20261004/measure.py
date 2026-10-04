#!/usr/bin/env python3
"""Compare complete PageCache paths without provider requests or dependencies."""
import argparse
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load Python source: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blob_id(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def stored_pair(modules, directory: Path, issues, etag='"v1"'):
    caches = [mod.PageCache(directory / str(i), "synthetic-benchmark")
              for i, mod in enumerate(modules)]
    statuses = [cache.store("owner/repo", 1, etag, issues, False) for cache in caches]
    assert statuses[0] == statuses[1], statuses
    contents = [sorted(p.read_bytes() for p in cache.root.glob("*.json")) for cache in caches]
    assert contents[0] == contents[1]
    outputs = [cache.load("owner/repo", 1) for cache in caches]
    assert outputs[0] == outputs[1]
    return caches, statuses[0], contents[0]


def compatibility(modules, root: Path):
    ordinary = [{"number": i, "body": "bounty", "nested": {"x": 1.25, "y": None}}
                for i in range(20)]
    cases = [
        (ordinary, '"v1"', "stored"),
        (ordinary, '""', "stored"),
        ([{"body": "a" * 20000 + "\U0001f680" * 3000}] * 100, 'W/"unicode"', "stored"),
        ([{"body": "a" * 120000}] * 100, '"large"', "skipped"),
        ([{"body": "a" * 120000}] * 99 + [{"bad": float("nan")}], '"bad"', "error"),
    ]
    for i, (issues, etag, expected) in enumerate(cases):
        _, status, _ = stored_pair(modules, root / f"compat-{i}", issues, etag)
        assert status == expected, (i, status)
    caches, _, contents = stored_pair(modules, root / "encodings", ordinary)
    text = contents[0].decode("utf-8")
    expected = caches[0].load("owner/repo", 1)
    encodings = ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be",
                 "utf-32", "utf-32-le", "utf-32-be")
    paths = [next(cache.root.glob("*.json")) for cache in caches]
    for encoding in encodings:
        for cache, path in zip(caches, paths):
            path.write_bytes(text.encode(encoding))
            assert cache.load("owner/repo", 1) == expected, encoding
    damaged = json.loads(text)
    damaged["sha256"] = "0" * 64
    for payload in (b"", b"{", b"\xff", json.dumps(damaged).encode(),
                    b" " * (modules[0]._MAX_BYTES + 1)):
        for cache, path in zip(caches, paths):
            path.write_bytes(payload)
            assert cache.load("owner/repo", 1) == (None, True)
    for cache, path in zip(caches, paths):
        path.unlink()
        assert cache.load("owner/repo", 1) == (None, False)
    return {"store_profiles": len(cases), "load_encodings": list(encodings),
            "invalid_load_profiles": 5, "miss": "preserved", "disk_bytes": "equal"}


def measure(modules, root: Path, issues):
    caches, status, contents = stored_pair(modules, root, issues)
    assert status == "stored"
    result = {"file_bytes": len(contents[0]), "issues": len(issues), "variants": {}}
    for name, cache in zip(("before", "after"), caches):
        gc.collect()
        tracemalloc.start()
        value = cache.load("owner/repo", 1)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert value[0]["issues"] == issues and value[1] is False
        del value
        result["variants"][name] = {"peak_traced_bytes": peak, "seconds": []}
    calls_per_timing = 25 if result["file_bytes"] < 100000 else 1
    result["calls_per_timing"] = calls_per_timing
    # Alternating untraced runs; disk-cache-warm complete load, not just hashing.
    for repeat in range(5):
        for index in ((0, 1) if repeat % 2 == 0 else (1, 0)):
            gc.collect()
            started = time.perf_counter()
            for _ in range(calls_per_timing):
                value = caches[index].load("owner/repo", 1)
            elapsed = (time.perf_counter() - started) / calls_per_timing
            assert value[0]["issues"] == issues and value[1] is False
            del value
            result["variants"][("before", "after")[index]]["seconds"].append(elapsed)
    for variant in result["variants"].values():
        variant["median_seconds"] = statistics.median(variant["seconds"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    modules = [load_module(args.baseline, "cache_before"),
               load_module(args.candidate, "cache_after")]
    report = {"python": sys.version, "platform": platform.platform(),
              "baseline_blob": blob_id(args.baseline), "candidate_blob": blob_id(args.candidate),
              "measurement": "tracemalloc peak and five untraced alternating complete loads"}
    with tempfile.TemporaryDirectory(prefix="page-cache-bench-") as directory:
        root = Path(directory)
        report["compatibility"] = compatibility(modules, root)
        report["profiles"] = {}
        for name, count, body in (
            ("small", 20, "Ordinary bounty issue " * 20),
            ("large_ascii", 100, "a" * 60000),
            ("large_mixed", 100, "a" * 35000 + "界" * 5000),
        ):
            issues = [{"number": i, "title": "issue", "body": body} for i in range(count)]
            report["profiles"][name] = measure(modules, root / name, issues)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
