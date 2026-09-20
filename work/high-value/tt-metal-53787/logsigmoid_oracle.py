#!/usr/bin/env python3
"""Host-only source model and acceptance oracle for tt-metal issue #53787.

The model intentionally separates:
* the current source branch structure and degree-8 polynomial;
* a non-fused binary32 arithmetic model for that polynomial;
* an accurate exp model in the positive tail so the one-term truncation error
  is visible without pretending to reproduce TT SFPU Approx::Fast; and
* the stable mathematical identity proposed by the issue.

This is not a Tenstorrent device simulator. It makes no device, performance,
assignment, merge, bounty, or payout claim.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import struct
from typing import Iterable

POLY_COEFFS = (
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
    value = float(value)
    try:
        return struct.unpack("!f", struct.pack("!f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def _finite_input(value: float) -> float:
    value32 = fp32(value)
    if not math.isfinite(value32):
        raise ValueError("acceptance oracle is finite binary32 inputs only")
    return value32


def _poly_nonfused_f32(value: float) -> float:
    """Evaluate the checked-in polynomial with binary32 mul then add rounding."""
    x = fp32(value)
    coeffs = tuple(fp32(c) for c in POLY_COEFFS)
    acc = coeffs[-1]
    for coefficient in reversed(coeffs[:-1]):
        acc = fp32(fp32(acc * x) + coefficient)
    return acc


def reference_logsigmoid(value: float) -> float:
    """Stable double reference over a binary32 input."""
    x = _finite_input(value)
    return min(x, 0.0) - math.log1p(math.exp(-abs(x)))


def stable_logsigmoid_f32(value: float) -> float:
    """Stable identity rounded to binary32 at the output."""
    return fp32(reference_logsigmoid(value))


def current_source_model_f32(value: float) -> float:
    """Model current checked-in branch arithmetic, excluding Approx::Fast error.

    The compute caller supplies exp(-x) before calculate_logsigmoid().  We use
    an accurate host exp and binary32 output here so any measured tail error is
    from the checked-in one-term approximation alone, not an invented model of
    the device's fast exponential.
    """
    original = _finite_input(value)
    try:
        exp_neg_x = fp32(math.exp(-original))
    except OverflowError:
        exp_neg_x = math.inf

    transformed = fp32(-original)
    result = original

    if transformed < -4.0:
        result = fp32(-exp_neg_x)
    elif transformed >= -4.0 and transformed < 4.0:
        result = fp32(-_poly_nonfused_f32(transformed))

    return result


def relative_error(actual: float, expected: float) -> float:
    if expected == 0.0:
        return abs(actual - expected)
    return abs((actual - expected) / expected)


def _ordered_f32_bits(value: float) -> int:
    rounded = fp32(value)
    if not math.isfinite(rounded):
        raise ValueError("ULP distance requires finite values")
    bits = struct.unpack("!I", struct.pack("!f", rounded))[0]
    if bits & 0x80000000:
        return (~bits) & 0xFFFFFFFF
    return bits | 0x80000000


def ulp_distance_f32(a: float, b: float) -> int:
    """Exact representable-step distance between two finite binary32 values."""
    return abs(_ordered_f32_bits(a) - _ordered_f32_bits(b))


@dataclass(frozen=True)
class Result:
    x: float
    current: float
    stable: float
    reference: float
    current_relative_error: float
    current_ulp_vs_reference_f32: int
    stable_ulp_vs_reference_f32: int
    source_regime: str

    def json_dict(self) -> dict[str, object]:
        return asdict(self)


def source_regime(value: float) -> str:
    x = _finite_input(value)
    if x <= -4.0:
        return "raw-input-missing-negative-branch"
    if x <= 4.0:
        return "degree-8-polynomial"
    return "one-term-positive-tail"


def evaluate(value: float) -> Result:
    x = _finite_input(value)
    current = current_source_model_f32(x)
    reference = reference_logsigmoid(x)
    expected_f32 = fp32(reference)
    stable = stable_logsigmoid_f32(x)
    return Result(
        x=x,
        current=current,
        stable=stable,
        reference=reference,
        current_relative_error=relative_error(current, reference),
        current_ulp_vs_reference_f32=ulp_distance_f32(current, expected_f32),
        stable_ulp_vs_reference_f32=ulp_distance_f32(stable, expected_f32),
        source_regime=source_regime(x),
    )


def default_vectors() -> tuple[float, ...]:
    return (
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


def report(values: Iterable[float] = default_vectors()) -> list[dict[str, object]]:
    return [evaluate(value).json_dict() for value in values]


def _cli() -> int:
    print(json.dumps(report(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
