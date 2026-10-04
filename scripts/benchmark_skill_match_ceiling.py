# SPDX-License-Identifier: MIT
"""Check exact ranking and time the score-ceiling shortcut without network reads.

Run from a source checkout: python scripts/benchmark_skill_match_ceiling.py
The comparator is recommend() from source blob ad172535 (before this change).
Both paths use the current production text preparation, patterns and scorer.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from heapq import nlargest
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import random
from statistics import median
import sys
from timeit import timeit
from types import FunctionType
from unittest.mock import patch

# Measure the exact ranking module without bootstrapping unrelated policy code.
source = Path(__file__).resolve().parents[1] / "concierge" / "skill_matcher.py"
spec = spec_from_file_location("skill_match_ceiling_benchmark", source)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load matcher from {source}")
matcher = module_from_spec(spec)
spec.loader.exec_module(matcher)
_skill_patterns = matcher._skill_patterns
_score_text = matcher._score_text
_bounty_text = matcher._bounty_text


def before(bounties, skills, limit=10):
    """Previous bounded-heap implementation, without either early exit."""
    if limit <= 0 or not bounties:
        return []
    patterns = _skill_patterns(skills)
    if limit >= len(bounties):
        scored_all = [
            dict(bounty, match_score=(
                _score_text(_bounty_text(bounty), patterns) if patterns else 0.0
            ))
            for bounty in bounties
        ]
        scored_all.sort(key=lambda bounty: bounty["match_score"], reverse=True)
        return scored_all
    scored = (
        (_score_text(_bounty_text(bounty), patterns) if patterns else 0.0, bounty)
        for bounty in bounties
    )
    selected = nlargest(limit, scored, key=lambda item: item[0])
    return [dict(bounty, match_score=score) for score, bounty in selected]


# Use identical global lookups and the production functions on both timed paths.
before = FunctionType(before.__code__, matcher.__dict__, before.__name__, before.__defaults__)


def check_equivalence():
    rng = random.Random(20261004)
    texts = ["python testing", "python", "testing", "trustworthy specific", "", "Straße", "C++"]
    checked = 0
    for _ in range(80):
        rows = [
            {"id": n, "title": rng.choice(texts), "body": rng.choice(texts),
             "labels": [{"name": rng.choice(texts)}, None], "language": rng.choice(texts),
             "match_score": -1}
            for n in range(rng.randrange(0, 60))
        ]
        original = deepcopy(rows)
        for skills in ([], ["python"], ["python", "testing"], ["python", "python"], ["strasse", "c++"]):
            for limit in (-1, 0, 1, 3, 10, len(rows), len(rows) + 1):
                expected = before(rows, skills, limit)
                actual = matcher.recommend(rows, skills, limit)
                assert actual == expected, (skills, limit, actual, expected)
                assert rows == original
                assert all(actual_row is not source_row for actual_row in actual for source_row in rows)
                checked += 1
    # Tags can change between calls; no prepared-state cache is introduced.
    with patch.dict(matcher.SKILL_TAGS, {"dynamic": ["python"]}):
        rows = [{"title": "python"}, {"title": "testing"}]
        assert matcher.recommend(rows, ["dynamic"], 1)[0]["title"] == "python"
        matcher.SKILL_TAGS["dynamic"] = ["testing"]
        assert matcher.recommend(rows, ["dynamic"], 1)[0]["title"] == "testing"
        matcher.SKILL_TAGS["dynamic"] = []
        assert matcher.recommend(rows, ["dynamic"], 1) == before(rows, ["dynamic"], 1)
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--repeat", type=int, default=7)
    parser.add_argument("--number", type=int, default=3)
    args = parser.parse_args()
    if args.rows < 100 or args.repeat < 1 or args.number < 1:
        parser.error("rows must be >=100; repeat and number must be positive")
    checks = check_equivalence()
    cases = {
        "full_match_prefix": ([{"id": n, "title": "python testing"} for n in range(args.rows)], ["python", "testing"], 10),
        "full_match_every_tenth": ([{"id": n, "title": "python testing" if n % 10 == 0 else "python"} for n in range(args.rows)], ["python", "testing"], 91),
        "no_full_match": ([{"id": n, "title": "python"} for n in range(args.rows)], ["python", "testing"], args.rows),
        "empty_skills": ([{"id": n, "title": "python testing"} for n in range(args.rows)], [], 0),
    }
    results = []
    for name, (rows, skills, expected_scored) in cases.items():
        assert matcher.recommend(rows, skills, 10) == before(rows, skills, 10)
        counts = []
        samples = [[], []]
        for function in (before, matcher.recommend):
            with patch.object(matcher, "_bounty_text", wraps=matcher._bounty_text) as text:
                function(rows, skills, 10)
                counts.append(text.call_count)
        # Alternate execution order to reduce drift; no timed instrumentation.
        for run in range(args.repeat):
            order = (0, 1) if run % 2 == 0 else (1, 0)
            for index in order:
                function = (before, matcher.recommend)[index]
                samples[index].append(timeit(lambda: function(rows, skills, 10), number=args.number) / args.number)
        times = [median(values) for values in samples]
        assert counts[1] == expected_scored, (name, counts)
        results.append({"case": name, "rows": len(rows), "limit": 10,
                        "before_scored": counts[0], "after_scored": counts[1],
                        "before_ms": round(times[0] * 1000, 6), "after_ms": round(times[1] * 1000, 6),
                        "speedup": round(times[0] / times[1], 3)})
    print(json.dumps({"python": sys.version.split()[0], "equivalence_cases": checks,
                      "repeat": args.repeat, "number": args.number,
                      "workload": "controlled synthetic rows; no provider reads",
                      "module_loading": "isolated source; package bootstrap not run", "results": results}, indent=2))


if __name__ == "__main__":
    main()
