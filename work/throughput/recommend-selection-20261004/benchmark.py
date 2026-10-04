#!/usr/bin/env python3
"""Compare complete recommend calls on retained inputs, without provider traffic."""

import argparse
import copy
import gc
import hashlib
import json
from pathlib import Path
import platform
import statistics
import time
import tracemalloc
import types


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_source(path, data_root, name):
    module = types.ModuleType(name)
    # Both exact source versions use the same authoritative on-disk skill tags.
    module.__file__ = str(data_root / "concierge" / "skill_matcher.py")
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def measure_call(function, rows, skills, limit, trace):
    gc.collect()
    if trace:
        tracemalloc.start()
    start_cpu = time.process_time_ns()
    start_wall = time.perf_counter_ns()
    result = function(rows, skills, limit)
    wall_ns = time.perf_counter_ns() - start_wall
    cpu_ns = time.process_time_ns() - start_cpu
    peak = tracemalloc.get_traced_memory()[1] if trace else None
    if trace:
        tracemalloc.stop()
    return result, {"cpu_ms": cpu_ns / 1e6, "wall_ms": wall_ns / 1e6, "peak_bytes": peak}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    modules = {
        "baseline": load_source(args.baseline, args.data_root, "baseline"),
        "candidate": load_source(args.candidate, args.data_root, "candidate"),
    }
    assert modules["baseline"].SKILL_TAGS == modules["candidate"].SKILL_TAGS
    skills = list(modules["baseline"].SKILL_TAGS)
    datasets = {
        "retained_index": json.loads(args.index.read_text())["bounties"],
        "retained_bountyhub_catalog": json.loads(args.catalog.read_text())["listings"],
    }
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "source_sha256": {name: sha256(getattr(args, name)) for name in modules},
        "input_sha256": {"index": sha256(args.index), "catalog": sha256(args.catalog),
                         "skill_tags": sha256(args.data_root / "data" / "skill_tags.json")},
        "skills": skills,
        "measurement": "complete recommend call; imports, input parsing, serialization and gc.collect excluded",
        "sampling": "3 warmups, then 7 alternating pairs separately for untraced timing and traced allocation",
        "datasets": {},
    }
    for label, rows in datasets.items():
        initial = copy.deepcopy(rows)
        comparisons = 0
        for selected_skills in ([], ["python", "security"], skills, ["absent-skill"]):
            for limit in (-1, 0, 1, 10, len(rows), len(rows) + 1):
                before = modules["baseline"].recommend(rows, selected_skills, limit)
                after = modules["candidate"].recommend(rows, selected_skills, limit)
                assert encoded(before) == encoded(after), (label, selected_skills, limit)
                assert all(output is not source for output in after for source in rows)
                comparisons += 1
        assert rows == initial, "recommend mutated its input"
        expected = modules["baseline"].recommend(rows, skills, 10)
        for _ in range(3):
            for module in modules.values():
                assert module.recommend(rows, skills, 10) == expected
        measurements = {mode: {name: [] for name in modules} for mode in ("timing", "allocation")}
        for mode in measurements:
            for pair in range(7):
                order = ("baseline", "candidate") if pair % 2 == 0 else ("candidate", "baseline")
                for name in order:
                    actual, sample = measure_call(modules[name].recommend, rows, skills, 10, mode == "allocation")
                    assert encoded(actual) == encoded(expected)
                    measurements[mode][name].append(sample)
        medians = {
            name: {
                "cpu_ms": statistics.median(x["cpu_ms"] for x in measurements["timing"][name]),
                "wall_ms": statistics.median(x["wall_ms"] for x in measurements["timing"][name]),
                "peak_bytes": statistics.median(x["peak_bytes"] for x in measurements["allocation"][name]),
            }
            for name in modules
        }
        result["datasets"][label] = {
            "rows": len(rows), "limit": 10, "output_rows": len(expected),
            "compatibility_comparisons": comparisons, "output_sha256": hashlib.sha256(encoded(expected)).hexdigest(),
            "output_bytes": len(encoded(expected)), "medians": medians, "samples": measurements,
        }
    # Independent expected ordering, score overwrite, shallow-copy and input checks.
    tie_rows = [{"id": i, "match_score": 99, "labels": []} for i in range(20)]
    for limit in (1, 10, 20, 25):
        output = modules["candidate"].recommend(tie_rows, [], limit)
        assert [row["id"] for row in output] == list(range(min(limit, 20)))
        assert all(row["match_score"] == 0.0 for row in output)
        assert all(row["match_score"] == 99 for row in tie_rows)
        if output:
            assert output[0] is not tie_rows[0] and output[0]["labels"] is tie_rows[0]["labels"]
    result["independent_tie_copy_cases"] = 4
    result["limits"] = [
        "Retained catalogs, not newly fetched live eligibility or claims.",
        "Peak traced Python allocation is not process RSS or fleet-wide memory.",
        "Timing is shared-host local CPU/wall time, not provider or end-to-end latency.",
        "Allocation/timing samples use the documented default limit of 10; other limits have output checks only.",
    ]
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"datasets": {name: {k: v for k, v in data.items() if k != "samples"}
                                  for name, data in result["datasets"].items()},
                      "independent_tie_copy_cases": 4}, indent=2))


if __name__ == "__main__":
    main()
