#!/usr/bin/env python3
"""Host/reference oracle for tenstorrent/tt-metal issue #55130.

This file checks the mathematical exact-GELU acceptance contract and preserves
the canonical issue's observed legacy device outputs as evidence vectors.
It does NOT model Tenstorrent hardware, the LLK fast approximation, or device
performance, and it grants no bounty/assignment/payment authority.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from typing import Iterable

UPSTREAM_MAIN = "708e7f58aef9d089f0e5097d4a772c7c8ee129ea"
ISSUE_NUMBER = 55130
REWARD_USD = 5000
ASSIGNEE = "AJ0070"

SOURCE_BLOBS = {
    "binary_composite.hpp": "401113560eb8d583c0f93925073dfcb4b4e670a9",
    "binary_nanobind.cpp": "db757a5cae9c3c286735f12ddd21f3f7b10372aa",
    "binary_composite_op.cpp": "10e5caf0a69738e89db904b5d91206467ac490b3",
    "binary_ng_device_operation.cpp": "bbf64d54b15ec9ec6fa4e6aaab175a9dac435c6e",
    "binary_ng_device_operation.hpp": "d5eda4d468b0ca9701678f3872543a5299909204",
    "binary_ng_program_factory.cpp": "4cdd7906cd18a7457a1efa2c957daab0a4fffb6e",
}

LEGACY_OBSERVED = {
    -3.0059: 0.0,
    -0.5034: -0.16347304,
    0.5034: 0.339948267,
    3.0059: 3.0058651,
}

# The issue table displays x to four decimals while its reference/output values\n# come from the underlying linspace sample. Treat these pairs as rounded table\n# evidence, not an assertion that exact_gelu(displayed_x) is bit-identical.\nISSUE_TORCH_REFERENCE = {
    -3.0059: -0.00398016,
    -0.5034: -0.15471851,
    0.5034: 0.348702799,
    3.0059: 3.00188493,
}


def _finite(value: float) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("finite input required")
    return result


def exact_gelu(value: float) -> float:
    x = _finite(value)
    return 0.5 * x * (1.0 + math.erf(x / math.sqrt(2.0)))


def exact_bias_gelu(a: float, bias: float) -> float:
    return exact_gelu(_finite(a) + _finite(bias))


def standard_tanh_gelu(value: float) -> float:
    """Mathematical contrast only; not a Tenstorrent hardware model."""
    x = _finite(value)
    inner = math.sqrt(2.0 / math.pi) * (x + 0.044715 * x * x * x)
    return 0.5 * x * (1.0 + math.tanh(inner))


@dataclass(frozen=True)
class Vector:
    x: float
    legacy_observed: float
    exact_reference: float
    exact_host: float
    legacy_abs_error: float
    exact_host_abs_error: float


def vector(value: float) -> Vector:
    x = _finite(value)
    legacy = LEGACY_OBSERVED[x]
    expected = ISSUE_TORCH_REFERENCE[x]
    host = exact_gelu(x)
    return Vector(
        x=x,
        legacy_observed=legacy,
        exact_reference=expected,
        exact_host=host,
        legacy_abs_error=abs(legacy - expected),
        exact_host_abs_error=abs(host - expected),
    )


def report(values: Iterable[float] = LEGACY_OBSERVED) -> dict[str, object]:
    rows = [asdict(vector(value)) for value in values]
    return {
        "issue": ISSUE_NUMBER,
        "reward_usd": REWARD_USD,
        "assignee": ASSIGNEE,
        "upstream_main": UPSTREAM_MAIN,
        "source_blobs": dict(SOURCE_BLOBS),
        "authority": "host-reference-only",
        "rows": rows,
        "max_legacy_abs_error_on_issue_samples": max(row["legacy_abs_error"] for row in rows),
        "max_exact_host_abs_error_on_issue_samples": max(row["exact_host_abs_error"] for row in rows),
    }


def _cli() -> int:
    print(json.dumps(report(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
