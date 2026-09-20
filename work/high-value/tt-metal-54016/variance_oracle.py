#!/usr/bin/env python3
"""Host-only numerical oracle for tt-metal issue #54016.

This is deliberately not a device simulator.  It provides:
* high-precision Decimal population/sample variance references;
* a deterministic FP32/BF16-input + FP32-accumulator model of the proposed
  shifted two-pass formulation;
* a deliberately unstable one-pass E[x^2]-E[x]^2 comparator; and
* adversarial vectors that expose large-common-offset cancellation.

No Tenstorrent hardware or performance behavior is claimed by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
import math
import struct
from typing import Callable, Iterable, Sequence

Quantizer = Callable[[float], float]


def fp32(value: float) -> float:
    """Round to IEEE-754 binary32, returning a Python float carrying that value."""
    return struct.unpack("!f", struct.pack("!f", float(value)))[0]


def bf16_rne(value: float) -> float:
    """Round a finite value to bfloat16 using round-to-nearest-even, return float32 value."""
    bits = struct.unpack("!I", struct.pack("!f", fp32(value)))[0]
    exponent = bits & 0x7F800000
    mantissa = bits & 0x007FFFFF
    if exponent == 0x7F800000:
        rounded = bits & 0xFFFF0000
        if mantissa and (rounded & 0x007F0000) == 0:
            rounded |= 0x00010000
    else:
        lsb = (bits >> 16) & 1
        rounded = (bits + 0x7FFF + lsb) & 0xFFFF0000
    return struct.unpack("!f", struct.pack("!I", rounded))[0]


def _validate(values: Sequence[float], correction: int) -> None:
    if not values:
        raise ValueError("variance requires at least one value")
    if correction < 0:
        raise ValueError("correction must be non-negative")
    if len(values) - correction <= 0:
        raise ValueError("correction must be smaller than sample count")


def decimal_variance(values: Iterable[object], correction: int = 0) -> Decimal:
    """High-precision population/sample variance using decimal values from str(value)."""
    xs = [Decimal(str(x)) for x in values]
    _validate(xs, correction)
    with localcontext() as ctx:
        ctx.prec = 80
        n = Decimal(len(xs))
        mean = sum(xs, Decimal(0)) / n
        m2 = sum((x - mean) * (x - mean) for x in xs)
        return +(m2 / Decimal(len(xs) - correction))


def shifted_two_pass(
    values: Sequence[float],
    correction: int = 0,
    *,
    input_quantizer: Quantizer = fp32,
    accumulator_quantizer: Quantizer = fp32,
) -> float:
    """Deterministic host arithmetic model of shifted two-pass statistics.

    Inputs are first rounded through ``input_quantizer``; every explicit arithmetic
    accumulation is rounded through ``accumulator_quantizer``.  This makes the
    cancellation experiment reproducible, but it is not a cycle-accurate SFPU model.
    """
    _validate(values, correction)
    xs = [input_quantizer(x) for x in values]
    aq = accumulator_quantizer
    shift = xs[0]

    centered_sum = aq(0.0)
    for x in xs:
        centered_sum = aq(centered_sum + aq(x - shift))
    centered_mean = aq(centered_sum / aq(float(len(xs))))

    m2 = aq(0.0)
    for x in xs:
        delta = aq(aq(x - shift) - centered_mean)
        m2 = aq(m2 + aq(delta * delta))
    return aq(m2 / aq(float(len(xs) - correction)))


def naive_one_pass(
    values: Sequence[float],
    correction: int = 0,
    *,
    input_quantizer: Quantizer = fp32,
    accumulator_quantizer: Quantizer = fp32,
) -> float:
    """Unstable E[x^2]-E[x]^2 comparator, useful only as a hostile baseline."""
    _validate(values, correction)
    xs = [input_quantizer(x) for x in values]
    aq = accumulator_quantizer
    total = aq(0.0)
    total_sq = aq(0.0)
    for x in xs:
        total = aq(total + x)
        total_sq = aq(total_sq + aq(x * x))
    n = aq(float(len(xs)))
    mean = aq(total / n)
    variance = aq(aq(total_sq / n) - aq(mean * mean))
    if correction:
        variance = aq(variance * aq(len(xs) / float(len(xs) - correction)))
    return variance


@dataclass(frozen=True)
class Vector:
    name: str
    values: tuple[float, ...]
    rationale: str


def vectors() -> tuple[Vector, ...]:
    def ramp(base: float, step: float, n: int) -> tuple[float, ...]:
        return tuple(base + (i - n / 2) * step for i in range(n))

    return (
        Vector(
            "fp32_large_offset_tiny_spread",
            ramp(1_000_000.0, 0.125, 32),
            "FP32-representable low variance around a large common offset; naive one-pass catastrophically cancels.",
        ),
        Vector(
            "fp32_negative_offset",
            tuple(-x for x in ramp(65_536.0, 0.015625, 32)),
            "Same cancellation class with a negative common offset.",
        ),
        Vector(
            "fp32_offset_1e8_ulp_scale",
            ramp(100_000_000.0, 8.0, 64),
            "Spread stays representable near 1e8 while the raw-square baseline can become negative from cancellation.",
        ),
        Vector(
            "constant",
            tuple([4096.0] * 32),
            "Zero-variance invariant; result must be exactly non-negative zero before epsilon handling.",
        ),
        Vector(
            "zero_crossing",
            (-4.0, -2.0, -1.0, -0.0, 0.0, 1.0, 2.0, 4.0),
            "Ordinary centered data catches selector regressions that only work for large positive offsets.",
        ),
        Vector(
            "alternating",
            tuple(1_000_000.0 + (0.5 if i % 2 else -0.5) for i in range(64)),
            "Repeated two-point spread stresses accumulation without relying on monotonic order.",
        ),
    )


def summarize() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for vector in vectors():
        for correction in (0, 1):
            ref = float(decimal_variance(vector.values, correction))
            shifted = shifted_two_pass(vector.values, correction)
            naive = naive_one_pass(vector.values, correction)
            rows.append(
                {
                    "name": vector.name,
                    "correction": correction,
                    "reference": ref,
                    "shifted_fp32": shifted,
                    "naive_fp32": naive,
                    "shifted_abs_error": abs(shifted - ref),
                    "naive_abs_error": abs(naive - ref),
                    "shifted_finite_nonnegative": math.isfinite(shifted) and shifted >= 0.0,
                }
            )
    return rows


if __name__ == "__main__":
    for row in summarize():
        print(row)
