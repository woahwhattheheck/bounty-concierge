#!/usr/bin/env python3
"""Host-only numerical oracle for tenstorrent/tt-metal issue #52037.

The current tt-metal LOGADDEXP / LOGADDEXP2 compositions apply EXP/EXP2
to each operand before adding and taking LOG/LOG2.  This module models that
failure mode with binary32 intermediates and optional flush-to-zero (FTZ),
then provides the stable max-plus-correction formulation used for acceptance
vectors.

This is not a Tenstorrent device simulator and makes no hardware,
performance, assignment, merge, bounty, or payout claim.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import struct
from typing import Callable, Iterable, Sequence

F32_MIN_NORMAL = 1.1754943508222875e-38
LN2 = math.log(2.0)


def fp32(value: float) -> float:
    """Round to IEEE-754 binary32, preserving infinities."""
    value = float(value)
    try:
        return struct.unpack("!f", struct.pack("!f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def fp32_intermediate(value: float, *, flush_subnormals: bool = True) -> float:
    """Round to binary32 and optionally flush subnormal intermediates to zero."""
    rounded = fp32(value)
    if (
        flush_subnormals
        and math.isfinite(rounded)
        and rounded != 0.0
        and abs(rounded) < F32_MIN_NORMAL
    ):
        return math.copysign(0.0, rounded)
    return rounded


def _finite_pair(a: float, b: float) -> tuple[float, float]:
    a32, b32 = fp32(a), fp32(b)
    if not (math.isfinite(a32) and math.isfinite(b32)):
        raise ValueError("oracle contract is finite binary32 inputs only")
    return a32, b32


def _exp_f32(value: float, *, base2: bool, flush_subnormals: bool) -> float:
    try:
        expanded = math.pow(2.0, value) if base2 else math.exp(value)
    except OverflowError:
        expanded = math.inf
    return fp32_intermediate(expanded, flush_subnormals=flush_subnormals)


def naive_logaddexp_f32(
    a: float,
    b: float,
    *,
    flush_subnormals: bool = True,
) -> float:
    """Model the current EXP -> ADD -> LOG binary32 composition."""
    a32, b32 = _finite_pair(a, b)
    lhs = _exp_f32(a32, base2=False, flush_subnormals=flush_subnormals)
    rhs = _exp_f32(b32, base2=False, flush_subnormals=flush_subnormals)
    summed = fp32_intermediate(lhs + rhs, flush_subnormals=flush_subnormals)
    if summed == 0.0:
        return -math.inf
    return fp32(math.log(summed))


def naive_logaddexp2_f32(
    a: float,
    b: float,
    *,
    flush_subnormals: bool = True,
) -> float:
    """Model the current EXP2 -> ADD -> LOG2 binary32 composition."""
    a32, b32 = _finite_pair(a, b)
    lhs = _exp_f32(a32, base2=True, flush_subnormals=flush_subnormals)
    rhs = _exp_f32(b32, base2=True, flush_subnormals=flush_subnormals)
    summed = fp32_intermediate(lhs + rhs, flush_subnormals=flush_subnormals)
    if summed == 0.0:
        return -math.inf
    return fp32(math.log2(summed))


def reference_logaddexp(a: float, b: float) -> float:
    """Double-precision stable reference for finite binary32 inputs."""
    a32, b32 = _finite_pair(a, b)
    hi = max(a32, b32)
    return hi + math.log1p(math.exp(-abs(a32 - b32)))


def reference_logaddexp2(a: float, b: float) -> float:
    """Double-precision base-2 stable reference for finite binary32 inputs."""
    a32, b32 = _finite_pair(a, b)
    hi = max(a32, b32)
    correction = math.log1p(math.exp(-abs(a32 - b32) * LN2)) / LN2
    return hi + correction


def stable_logaddexp_f32(a: float, b: float) -> float:
    """Stable max(a,b) + log1p(exp(-abs(a-b))) rounded to binary32."""
    return fp32(reference_logaddexp(a, b))


def stable_logaddexp2_f32(a: float, b: float) -> float:
    """Stable base-2 equivalent rounded to binary32."""
    return fp32(reference_logaddexp2(a, b))


@dataclass(frozen=True)
class Vector:
    op: str
    a: float
    b: float


@dataclass(frozen=True)
class Result:
    op: str
    a: float
    b: float
    naive: float
    stable: float
    reference: float
    lower_bound: float
    upper_bound: float

    def json_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in ("naive", "stable", "reference", "lower_bound", "upper_bound"):
            value = payload[key]
            if isinstance(value, float) and not math.isfinite(value):
                payload[key] = "inf" if value > 0 else "-inf"
        return payload


def default_vectors() -> tuple[Vector, ...]:
    """Issue rows plus boundary/large-magnitude acceptance vectors."""
    return (
        Vector("logaddexp", 100.0, 0.0),
        Vector("logaddexp", 89.0, 0.0),
        Vector("logaddexp", 90.0, 89.0),
        Vector("logaddexp", 100.0, 100.0),
        Vector("logaddexp", 200.0, 199.0),
        Vector("logaddexp", -100.0, -100.0),
        Vector("logaddexp", 88.0, 88.0),
        Vector("logaddexp", 5.0, 3.0),
        Vector("logaddexp", 88.7, 0.0),
        Vector("logaddexp", 1000.0, 999.0),
        Vector("logaddexp", 10000.0, 10000.0),
        Vector("logaddexp", -10000.0, -10000.0),
        Vector("logaddexp", -10000.0, 10000.0),
        Vector("logaddexp2", 126.0, 126.0),
        Vector("logaddexp2", 128.0, 128.0),
        Vector("logaddexp2", -126.0, -126.0),
        Vector("logaddexp2", -128.0, -128.0),
        Vector("logaddexp2", 10000.0, 10000.0),
        Vector("logaddexp2", -10000.0, -10000.0),
        Vector("logaddexp2", -10000.0, 10000.0),
    )


def evaluate(vector: Vector) -> Result:
    if vector.op == "logaddexp":
        naive = naive_logaddexp_f32(vector.a, vector.b)
        stable = stable_logaddexp_f32(vector.a, vector.b)
        reference = reference_logaddexp(vector.a, vector.b)
        increment = LN2
    elif vector.op == "logaddexp2":
        naive = naive_logaddexp2_f32(vector.a, vector.b)
        stable = stable_logaddexp2_f32(vector.a, vector.b)
        reference = reference_logaddexp2(vector.a, vector.b)
        increment = 1.0
    else:
        raise ValueError(f"unknown op: {vector.op}")

    a32, b32 = _finite_pair(vector.a, vector.b)
    lower = max(a32, b32)
    return Result(
        op=vector.op,
        a=a32,
        b=b32,
        naive=naive,
        stable=stable,
        reference=reference,
        lower_bound=lower,
        upper_bound=lower + increment,
    )


def report(vectors: Iterable[Vector] = default_vectors()) -> list[dict[str, object]]:
    return [evaluate(vector).json_dict() for vector in vectors]


def _cli() -> int:
    print(json.dumps(report(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
