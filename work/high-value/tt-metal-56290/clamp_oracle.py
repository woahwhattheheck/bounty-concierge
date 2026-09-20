#!/usr/bin/env python3
"""Exact host oracle for tt-metal #56290 quantize/requantize semantics.

The issue contract defines round-to-nearest-even followed by saturation for uint8.
This module uses Decimal so binary floating-point noise does not decide tie cases.
It is a host/reference artifact only; it does not emulate Tensix/SFPU execution.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_HALF_EVEN
import json
from typing import Iterable, Literal

DType = Literal["uint8", "int8", "int32"]

_LIMITS: dict[DType, tuple[int, int]] = {
    "uint8": (0, 255),
    "int8": (-128, 127),
    "int32": (-(2**31), 2**31 - 1),
}


def D(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def round_even(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def saturate(value: int, dtype: DType) -> int:
    lo, hi = _LIMITS[dtype]
    return min(hi, max(lo, value))


def quantize(value: object, scale: object, zero_point: int, dtype: DType = "uint8") -> int:
    scale_d = D(scale)
    if scale_d <= 0:
        raise ValueError("scale must be positive")
    transformed = D(value) / scale_d + D(zero_point)
    return saturate(round_even(transformed), dtype)


def requantize(
    value: int,
    input_scale: object,
    input_zero_point: int,
    output_scale: object,
    output_zero_point: int,
    dtype: DType = "uint8",
) -> int:
    in_s, out_s = D(input_scale), D(output_scale)
    if in_s <= 0 or out_s <= 0:
        raise ValueError("scales must be positive")
    transformed = (D(value) - D(input_zero_point)) * in_s / out_s + D(output_zero_point)
    return saturate(round_even(transformed), dtype)


@dataclass(frozen=True)
class QuantVector:
    name: str
    value: str
    scale: str
    zero_point: int
    dtype: DType

    @property
    def expected(self) -> int:
        return quantize(self.value, self.scale, self.zero_point, self.dtype)


@dataclass(frozen=True)
class RequantVector:
    name: str
    value: int
    input_scale: str
    input_zero_point: int
    output_scale: str
    output_zero_point: int
    dtype: DType

    @property
    def expected(self) -> int:
        return requantize(
            self.value,
            self.input_scale,
            self.input_zero_point,
            self.output_scale,
            self.output_zero_point,
            self.dtype,
        )


def quant_vectors() -> tuple[QuantVector, ...]:
    return (
        QuantVector("u8_far_negative", "-1000", "1", 0, "uint8"),
        QuantVector("u8_just_below_zero", "-0.51", "1", 0, "uint8"),
        QuantVector("u8_negative_tie", "-0.5", "1", 0, "uint8"),
        QuantVector("u8_zero", "0", "1", 0, "uint8"),
        QuantVector("u8_half_even_down", "0.5", "1", 0, "uint8"),
        QuantVector("u8_half_even_up", "1.5", "1", 0, "uint8"),
        QuantVector("u8_last_in_range", "254.49", "1", 0, "uint8"),
        QuantVector("u8_upper_tie", "254.5", "1", 0, "uint8"),
        QuantVector("u8_overflow", "300", "1", 0, "uint8"),
        QuantVector("u8_nonzero_zp_lower", "-3.1", "0.5", 6, "uint8"),
        QuantVector("u8_nonzero_zp_middle", "10.2", "0.2", 10, "uint8"),
        QuantVector("u8_nonzero_zp_upper", "100", "0.25", 20, "uint8"),
        QuantVector("i8_lower_saturation", "-500", "1", 0, "int8"),
        QuantVector("i8_negative_in_range", "-12.5", "1", 0, "int8"),
        QuantVector("i8_positive_in_range", "126.5", "1", 0, "int8"),
        QuantVector("i8_upper_saturation", "500", "1", 0, "int8"),
    )


def requant_vectors() -> tuple[RequantVector, ...]:
    return (
        RequantVector("u8_negative_source", -1000, "1", 0, "1", 0, "uint8"),
        RequantVector("u8_negative_after_input_zp", 9, "2", 10, "1", 0, "uint8"),
        RequantVector("u8_exact_zero", 10, "2", 10, "1", 0, "uint8"),
        RequantVector("u8_in_range", 64, "1", 0, "1", 0, "uint8"),
        RequantVector("u8_scaled_in_range", 64, "0.5", 0, "0.25", 10, "uint8"),
        RequantVector("u8_upper_saturation", 1000, "1", 0, "1", 0, "uint8"),
        RequantVector("u8_output_zp_lower", 0, "1", 10, "2", 3, "uint8"),
        RequantVector("u8_half_even_down", 1, "1", 0, "2", 0, "uint8"),
        RequantVector("u8_half_even_up", 3, "1", 0, "2", 0, "uint8"),
        RequantVector("i8_lower_saturation", -500, "1", 0, "1", 0, "int8"),
        RequantVector("i8_negative_in_range", -25, "1", 0, "2", 0, "int8"),
        RequantVector("i8_positive_in_range", 125, "2", 0, "2", 0, "int8"),
        RequantVector("i8_upper_saturation", 500, "1", 0, "1", 0, "int8"),
    )


def vector_table() -> dict[str, list[dict[str, object]]]:
    q = [{**asdict(v), "expected": v.expected} for v in quant_vectors()]
    rq = [{**asdict(v), "expected": v.expected} for v in requant_vectors()]
    return {"quantize": q, "requantize": rq}


def render_markdown() -> str:
    lines = ["# Exact-output clamp vectors", "", "## quantize", "", "|name|value|scale|zp|dtype|expected|", "|---|---:|---:|---:|---|---:|"]
    for v in quant_vectors():
        lines.append(f"|{v.name}|{v.value}|{v.scale}|{v.zero_point}|{v.dtype}|{v.expected}|")
    lines += ["", "## requantize", "", "|name|value|in scale|in zp|out scale|out zp|dtype|expected|", "|---|---:|---:|---:|---:|---:|---|---:|"]
    for v in requant_vectors():
        lines.append(
            f"|{v.name}|{v.value}|{v.input_scale}|{v.input_zero_point}|{v.output_scale}|{v.output_zero_point}|{v.dtype}|{v.expected}|"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(json.dumps(vector_table(), indent=2, sort_keys=True))
