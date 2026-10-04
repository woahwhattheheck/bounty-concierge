"""Reproduce limited-browse selection against a retained prior CLI file; no network."""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import platform
import random
import statistics
import time
import tracemalloc
from types import SimpleNamespace
from unittest.mock import patch

from concierge import cli
from concierge import bounty_index


def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-file", required=True, type=Path)
    args = parser.parse_args()
    source = args.before_file.read_bytes()
    assert blob(source) == "9a5ac2c9ae3c4a06907b357fb4d0f99e31183154"
    spec = importlib.util.spec_from_file_location("browse_before", args.before_file)
    before = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(before)
    root = Path(cli.__file__).resolve().parents[1]
    retained = json.loads((root / "data/bounty_index.json").read_text())
    actual_rows = retained["bounties"]

    def options(**kwargs):
        return SimpleNamespace(skill=None, tier=None, min_rtc=None, max_rtc=None,
                               **kwargs)

    def old(rows, selection):
        filtered = before._filter_bounties(rows, selection)
        return len(filtered), filtered[:selection.limit]

    def new(rows, selection):
        filtered = cli._filter_bounties(rows, selection, presort=False)
        return len(filtered), cli._limit_bounties(filtered, selection.limit)

    def same(rows, selection):
        old_count, old_rows = old(rows, selection)
        new_count, new_rows = new(rows, selection)
        assert old_count == new_count and len(old_rows) == len(new_rows)
        assert all(a is b for a, b in zip(old_rows, new_rows))
        return old_count

    cases = []
    sample = [{"repo": "example/repo", "number": i + 1, "title": str(i),
               "skills": ["documentation", "ci-cd"], "difficulty": "standard",
               "reward_rtc": value} for i, value in enumerate([10, 10, 0, 5, 0, 10])]
    sample[4]["reward_evidence"] = {"status": "matched", "amount_rtc": 0}
    sample.append(dict(sample[0], number=7, reward_evidence={"status": "no_match"}))
    untouched = copy.deepcopy(sample)
    for limit in (0, 1, 3, len(sample), len(sample) + 1):
        same(sample, options(limit=limit))
    cases.append("stable ties, explicit zero, unknown evidence and display boundaries")
    for change in ({"skill": "docs"}, {"skill": "ci/cd"}, {"tier": "STANDARD"},
                   {"min_rtc": 0, "max_rtc": 10}, {"min_rtc": 20}):
        selection = options(limit=3)
        for key, value in change.items():
            setattr(selection, key, value)
        same(sample, selection)
    assert sample == untouched
    assert cli._filter_bounties(sample, options(limit=3)) == before._filter_bounties(sample, options(limit=3))
    cases.append("filters, complete helper compatibility and immutable source rows")

    def command(module, argv):
        parsed = module._build_parser().parse_args(argv)
        out, err, code = io.StringIO(), io.StringIO(), 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                module._cmd_browse(parsed)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    offline = ["browse", "--index", str(root / "data/bounty_index.json"), "--report", "--limit", "10"]
    assert command(before, offline) == command(cli, offline)
    cases.append("actual offline command, full report and selection counts")
    report = dict(retained, complete=False, rate_limited=True,
                  retry_after_seconds=60, rate_limit_reset_at=None,
                  repositories=[{"repo": "example/repo", "status": "PAGE_LIMIT",
                                 "pages_fetched": 1, "bounty_count": len(actual_rows), "http_status": 200}],
                  started_at=retained["updated_at"], total_count=len(actual_rows))
    for mode in ("--report", "--json"):
        with patch.object(bounty_index, "fetch_bounties_report", return_value=report) as fetch:
            a = command(before, ["browse", mode, "--limit", "10"])
            b = command(cli, ["browse", mode, "--limit", "10"])
        assert a == b and a[0] == 2 and fetch.call_count == 2
    cases.append("partial live-command outputs/exits; one supplied collector result per invocation")
    sample[0]["reward_rtc"] = 100
    same(sample, options(limit=1))
    assert new(sample, options(limit=1))[1][0] is sample[0]
    cases.append("changed inputs observed without a result cache")

    rng = random.Random(20261004)
    large = [dict(sample[0], number=i+1, reward_rtc=rng.randrange(1000)) for i in range(100_000)]
    workloads = [("retained_index", actual_rows, options(limit=10)),
                 ("100k_top10", large, options(limit=10)),
                 ("100k_top1000", large, options(limit=1000))]
    bounded = options(limit=10)
    bounded.min_rtc = 500
    workloads.append(("100k_filtered_top10", large, bounded))
    results = []
    for label, rows, selection in workloads:
        count = same(rows, selection)
        timing = {"before": [], "after": []}
        for pair in range(5):
            for name, fn in (("before", old), ("after", new))[::1 if pair % 2 == 0 else -1]:
                start = time.perf_counter_ns()
                result = fn(rows, selection)
                timing[name].append((time.perf_counter_ns()-start) / 1e6)
                del result
        peaks = {}
        for name, fn in (("before", old), ("after", new)):
            tracemalloc.start()
            result = fn(rows, selection)
            _, peaks[name] = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            del result
        results.append({"workload": label, "rows": len(rows), "matches": count,
                        "limit": selection.limit, "samples_ms": timing,
                        "median_ms": {k: statistics.median(v) for k,v in timing.items()},
                        "selection_peak_traced_bytes": peaks})
    print(json.dumps({"python": platform.python_version(), "before_blob": blob(source),
                      "after_blob": blob(Path(cli.__file__).read_bytes()),
                      "checks": cases, "workloads": results,
                      "scope": "Local production filtering/selection only. Timings exclude source I/O, parsing, imports and display. Trace peaks exclude input allocation. Live command uses a supplied report, not a provider request."}, indent=2))


if __name__ == "__main__":
    main()
