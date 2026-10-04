#!/usr/bin/env python3
"""Compare the exact capture JSON writer with synthetic JSON, offline.

Runs only the production _write_json function and the complete secure_output
module. It intentionally does not import or execute preflight, capture replay,
HTTP transport, or collect_batch. No dependencies beyond Python's stdlib.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
from itertools import chain
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from typing import Any
from unittest.mock import patch


def git_blob(path: Path) -> str:
    raw = path.read_bytes()
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


def load_writer(root: Path):
    source = root / "concierge" / "bounty_capture_batch.py"
    secure = root / "concierge" / "secure_output.py"
    spec = importlib.util.spec_from_file_location("measured_secure_output", secure)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parsed = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    nodes = [n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name == "_write_json"]
    if len(nodes) != 1:
        raise ValueError("expected exactly one production _write_json function")
    namespace = {
        "json": json, "chain": chain, "Path": Path, "Any": Any,
        "create_exclusive_regular": module.create_exclusive_regular,
    }
    if hasattr(module, "create_exclusive_regular_chunks"):
        namespace["create_exclusive_regular_chunks"] = module.create_exclusive_regular_chunks
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    return namespace["_write_json"], {
        "writer_git_blob": git_blob(source), "secure_output_git_blob": git_blob(secure),
    }


def fixture(case: str) -> dict[str, Any]:
    # Shape-only capture data, not valid capture receipts or observed bounties.
    count, comments = {"small": (1, 20), "large": (1000, 50)}[case]
    return {"candidates": [
        {
            "repo": "synthetic/example", "number": i + 1,
            "observation": {"started_at": "2026-10-04T08:00:00Z", "request_count": 3},
            "comments": [
                {"id": i * comments + j, "body": f"Synthetic comment {i}:{j} " + "x" * 256,
                 "user": {"login": "synthetic-contributor"}, "created_at": "2026-10-04T08:00:00Z"}
                for j in range(comments)
            ],
        }
        for i in range(count)
    ]}


def worker(root: Path, case: str, mode: str) -> dict[str, Any]:
    writer, sources = load_writer(root)
    value = fixture(case)
    with tempfile.TemporaryDirectory(prefix="capture-json-bench-") as folder:
        output = Path(folder) / "supply.json"
        if mode == "memory":
            tracemalloc.start()
        started = time.perf_counter()
        writer(output, value)
        elapsed = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1] if mode == "memory" else None
        if mode == "memory":
            tracemalloc.stop()
        peak_rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Outside the measured region: byte identity and permission assertion.
        raw = output.read_bytes()
        expected = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        assert raw == expected
        assert output.stat().st_mode & 0o777 == 0o600
        return {
            **sources, "case": case, "mode": mode, "wall_seconds": elapsed,
            "traced_peak_bytes": peak, "peak_rss_kib_before_comparison": peak_rss_kib,
            "output_bytes": len(raw), "output_sha256": hashlib.sha256(raw).hexdigest(),
            "byte_identical": True, "mode_0600": True,
        }


def edge_checks(root: Path) -> dict[str, Any]:
    writer, sources = load_writer(root)
    valid = [None, {}, [], {"z": [True, False, 2.5, -0.0], "a": "é 漢字 😀\u2028\n\\\ud800"},
             {2: "numeric key", 1: "first"}, {"nested": [[], {}, {"x": None}]},
             "x" * 65533, "x" * 65534, "x" * 65535,
             {"large_unicode": "é 漢字 😀\ud800" * 20000}]
    cycle = []; cycle.append(cycle)
    invalid = [{"late": [1, float("nan")]}, {"late": float("inf")},
               {"late": object()}, cycle, {1: "a", "b": "mixed sort keys"}]
    with tempfile.TemporaryDirectory(prefix="capture-json-edges-") as folder:
        directory = Path(folder)
        for i, value in enumerate(valid):
            path = directory / f"valid-{i}.json"
            writer(path, value)
            assert path.read_bytes() == (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        for i, value in enumerate(invalid):
            path = directory / f"invalid-{i}.json"
            try:
                writer(path, value)
            except (TypeError, ValueError):
                assert not path.exists(), "serialization failure created an output leaf"
            else:
                raise AssertionError("invalid input was accepted")
        existing = directory / "existing.json"
        existing.write_bytes(b"keep")
        try:
            writer(existing, {"overwrite": True})
        except RuntimeError:
            assert existing.read_bytes() == b"keep"
        else:
            raise AssertionError("existing output was overwritten")
    return {**sources, "valid_byte_cases": len(valid), "invalid_no_leaf_cases": len(invalid),
            "existing_output_preserved": True}


def secure_checks(root: Path) -> dict[str, Any]:
    """Focused compatibility checks for the shared writer, not a project suite."""
    spec = importlib.util.spec_from_file_location(
        "checked_secure_output", root / "concierge" / "secure_output.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    variants = {"bytes": lambda p, value: module.create_exclusive_regular(p, value)}
    if hasattr(module, "create_exclusive_regular_chunks"):
        variants["chunks"] = lambda p, value: module.create_exclusive_regular_chunks(
            p, (value[:3], b"", value[3:]),
        )
    before_fds = len(os.listdir("/proc/self/fd")) if Path("/proc/self/fd").exists() else None
    checked = []
    for name, write in variants.items():
        with tempfile.TemporaryDirectory(prefix="capture-secure-checks-") as folder:
            directory = Path(folder)
            value = b"kept output bytes"
            original_write = os.write
            short_path = directory / "short"
            with patch.object(module.os, "write", side_effect=lambda fd, view: original_write(fd, view[:2])):
                write(short_path, value)
            assert short_path.read_bytes() == value
            checked.append(name + ":partial-writes")
            for fault, expected in (("zero-write", b""), ("fsync-error", value)):
                path = directory / fault
                context = (patch.object(module.os, "write", return_value=0) if fault == "zero-write"
                           else patch.object(module.os, "fsync", side_effect=OSError("controlled fsync failure")))
                with context:
                    try:
                        write(path, value)
                    except (OSError, module.SecureOutputError):
                        pass
                    else:
                        raise AssertionError("controlled output failure was lost")
                assert path.read_bytes() == expected
                checked.append(name + ":" + fault + "-retained")
            target = directory / "existing"
            target.write_bytes(b"original")
            link = directory / "leaf-link"
            link.symlink_to(target)
            real_parent = directory / "parent"
            real_parent.mkdir()
            parent_link = directory / "parent-link"
            parent_link.symlink_to(real_parent, target_is_directory=True)
            for label, path in (("existing", target), ("leaf-symlink", link),
                                ("parent-symlink", parent_link / "new")):
                try:
                    write(path, value)
                except module.SecureOutputError:
                    pass
                else:
                    raise AssertionError("unsafe destination accepted")
                assert target.read_bytes() == b"original"
                assert not (real_parent / "new").exists()
                checked.append(name + ":" + label + "-refused")
            assert short_path.stat().st_mode & 0o777 == 0o600
            if name == "bytes":
                invalid = directory / "invalid-payload"
                try:
                    module.create_exclusive_regular(invalid, bytearray(b"not bytes"))
                except module.SecureOutputError:
                    assert not invalid.exists()
                else:
                    raise AssertionError("bytes API accepted a non-bytes payload")
                checked.append("bytes:invalid-payload-no-leaf")
            else:
                def broken_chunks():
                    yield b"prefix"
                    raise ValueError("controlled iterator failure")
                late = directory / "late-iterator"
                try:
                    module.create_exclusive_regular_chunks(late, broken_chunks())
                except ValueError:
                    assert late.read_bytes() == b"prefix"
                else:
                    raise AssertionError("iterator failure was lost")
                checked.append("chunks:late-iterator-retained")
                bad = directory / "bad-chunk"
                try:
                    module.create_exclusive_regular_chunks(bad, (b"prefix", "not bytes"))
                except module.SecureOutputError:
                    assert bad.read_bytes() == b"prefix"
                else:
                    raise AssertionError("non-bytes chunk accepted")
                checked.append("chunks:invalid-chunk-retained")
    after_fds = len(os.listdir("/proc/self/fd")) if before_fds is not None else None
    assert before_fds == after_fds
    return {"checks": checked, "fd_count_before": before_fds, "fd_count_after": after_fds}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", nargs=3, metavar=("ROOT", "CASE", "MODE"))
    args = parser.parse_args()
    if args.worker:
        root, case, mode = args.worker
        print(json.dumps(worker(Path(root), case, mode), sort_keys=True))
        return 0
    if args.baseline is None or args.candidate is None or not 1 <= args.repeats <= 20:
        parser.error("provide --baseline and --candidate, with --repeats between 1 and 20")
    roots = {"baseline": args.baseline.resolve(), "candidate": args.candidate.resolve()}
    observations = []
    for case in ("small", "large"):
        for repeat in range(args.repeats):
            # Alternate ordering to reduce order bias, with a fresh process each run.
            order = ("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline")
            for variant in order:
                command = [sys.executable, __file__, "--worker", str(roots[variant]), case, "wall"]
                row = json.loads(subprocess.check_output(command, text=True))
                observations.append({"variant": variant, "repeat": repeat, **row})
        for variant, root in roots.items():
            command = [sys.executable, __file__, "--worker", str(root), case, "memory"]
            row = json.loads(subprocess.check_output(command, text=True))
            observations.append({"variant": variant, "repeat": None, **row})
    summary = []
    for case in ("small", "large"):
        by_variant = {}
        for variant in roots:
            rows = [r for r in observations if r["case"] == case and r["variant"] == variant]
            timed = [r for r in rows if r["mode"] == "wall"]
            measured = next(r for r in rows if r["mode"] == "memory")
            by_variant[variant] = {
                "median_wall_seconds": statistics.median(r["wall_seconds"] for r in timed),
                "median_peak_rss_kib": statistics.median(r["peak_rss_kib_before_comparison"] for r in timed),
                "traced_peak_bytes": measured["traced_peak_bytes"],
                "output_bytes": measured["output_bytes"], "output_sha256": measured["output_sha256"],
            }
        assert by_variant["baseline"]["output_sha256"] == by_variant["candidate"]["output_sha256"]
        before, after = by_variant["baseline"], by_variant["candidate"]
        summary.append({"case": case, **by_variant,
                        "wall_ratio_candidate_over_baseline": after["median_wall_seconds"] / before["median_wall_seconds"],
                        "traced_peak_reduction_percent": 100 * (1 - after["traced_peak_bytes"] / before["traced_peak_bytes"])})
    report = {
        "scope": "exact production _write_json and complete secure_output only; synthetic JSON; no network or end-to-end collector execution",
        "python": sys.version, "platform": platform.platform(),
        "baseline_commit": "2fefacd83611707f7c4a3c229bfdf513f1630e01",
        "wall_repeats_per_case_variant": args.repeats,
        "memory_method": "one separate tracemalloc run; fixture built before tracing; output comparison after tracing",
        "edge_checks": {variant: edge_checks(root) for variant, root in roots.items()},
        "secure_checks": {variant: secure_checks(root) for variant, root in roots.items()},
        "summary": summary, "observations": observations,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
