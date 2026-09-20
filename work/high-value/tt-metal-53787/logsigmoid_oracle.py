#!/usr/bin/env python3
"""Host-only acceptance oracle for tenstorrent/tt-metal issue #53787.

Models the control-flow defect still present in the pinned Wormhole/Blackhole
log_sigmoid SFPU headers and compares it with the stable mathematical identity.

This is not a device simulator. It makes no hardware, performance, assignment,
merge, bounty-entitlement, or payout claim.
"""
from __future__ import annotations

import json
import math
import struct
from typing import Iterable

UPSTREAM_MAIN = "708e7f58aef9d089f0e5097d4a772c7c8ee129ea"
WORMHOLE_HEADER_BLOB = "cdc5ad762c24219187ad8e6babe57a2d261474ff"
BLACKHOLE_HEADER_BLOB = "e2ca2e3e875ffcd78f1031df5cfdf4de59b9e0a9"
COMPUTE_KERNEL_BLOB = "5e6522f035fc2856e805997a678dbfa63a7735f4"
NIGHTLY_SWEEP_BLOB = "4653c5028a3df72684b11d87cbcd3b29bd4e9285"

POLY = (
    0.6924354434013367,
    0.49275708198547363,
    0.12142381817102432,
    0.0031102809589356184,
    -0.00330807245336473,
    -0.00028794066747650504,
    5.3185409342404455e-05,
    7.1853546614875086e-06,
    7.4961114648886e-08,
)


def fp32(value: float) -> float:
    """Round a Python float to IEEE-754 binary32."""
    value = float(value)
    try:
        return struct.unpack("!f", struct.pack("!f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def finite_fp32(value: float) -> float:
    rounded = fp32(value)
    if not math.isfinite(rounded):
        raise ValueError("oracle contract is finite binary32 input only")
    return rounded


def polynomial(value: float) -> float:
    """Evaluate the checked-in degree-8 coefficient set."""
    x = finite_fp32(value)
    total = 0.0
    power = 1.0
    for coefficient in POLY:
        total += coefficient * power
        power *= x
    return fp32(total)


def current_source_model(value: float) -> float:
    """Model the checked-in SFPU branch structure.

    The real compute path supplies exp(-x) from a fast-exp primitive. Here the
    exponential itself is evaluated accurately and rounded to fp32, which
    isolates the structural branch/truncation error instead of exaggerating it
    with implementation-specific fast-exp error.
    """
    original = finite_fp32(value)
    negated = fp32(-original)

    if negated < -4.0:
        exp_neg_x = fp32(math.exp(-original))
        return fp32(-exp_neg_x)

    if negated >= -4.0 and negated < 4.0:
        return fp32(-polynomial(negated))

    # This is the missing branch: original x <= -4 falls through unchanged.
    return original


def reference_logsigmoid(value: float) -> float:
    """Stable double-precision reference for finite binary32 input."""
    x = finite_fp32(value)
    return min(x, 0.0) - math.log1p(math.exp(-abs(x)))


def stable_logsigmoid_f32(value: float) -> float:
    return fp32(reference_logsigmoid(value))


def relative_error(actual: float, expected: float) -> float:
    if expected == 0.0:
        return abs(actual - expected)
    return abs((actual - expected) / expected)


def fp32_ulp_distance(left: float, right: float) -> int:
    """Bit-distance for finite fp32 values with the same sign."""
    a = finite_fp32(left)
    b = finite_fp32(right)
    if math.copysign(1.0, a) != math.copysign(1.0, b):
        raise ValueError("same-sign inputs required")
    ai = struct.unpack("!I", struct.pack("!f", a))[0]
    bi = struct.unpack("!I", struct.pack("!f", b))[0]
    return abs(ai - bi)


def legacy_nightly_domain_contains(value: float) -> bool:
    """Mirror the checked-in torch_random(low=-4, high=10) range."""
    x = finite_fp32(value)
    return -4.0 <= x <= 10.0


DEFAULT_VECTORS = (
    -30.0,
    -10.0,
    -5.0,
    -4.0001,
    -4.0,
    -3.9999,
    -1.0,
    0.0,
    1.0,
    3.9999,
    4.0,
    4.0001,
    5.0,
    10.0,
    30.0,
)


def report(values: Iterable[float] = DEFAULT_VECTORS) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for value in values:
        x = finite_fp32(value)
        current = current_source_model(x)
        stable = stable_logsigmoid_f32(x)
        reference = reference_logsigmoid(x)
        rows.append(
            {
                "x": x,
                "legacy_nightly_domain": legacy_nightly_domain_contains(x),
                "current_source_model": current,
                "stable_fp32": stable,
                "reference": reference,
                "current_relative_error": relative_error(current, reference),
                "stable_relative_error": relative_error(stable, reference),
                "stable_ulp_error": fp32_ulp_distance(stable, fp32(reference)),
            }
        )
    return rows


def _cli() -> int:
    print(json.dumps(report(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
