from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys


ORACLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "work"
    / "high-value"
    / "tt-metal-52037"
    / "logaddexp_oracle.py"
)
SPEC = importlib.util.spec_from_file_location("tt_metal_52037_logaddexp_oracle", ORACLE_PATH)
assert SPEC is not None and SPEC.loader is not None
oracle = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = oracle
SPEC.loader.exec_module(oracle)


def _f32_tolerance(reference: float) -> float:
    return max(2e-6, abs(reference) * 2e-7)


def test_reported_positive_overflow_is_intermediate_only():
    for a, b in [(100.0, 0.0), (89.0, 0.0), (90.0, 89.0), (200.0, 199.0)]:
        assert math.isinf(oracle.naive_logaddexp_f32(a, b))
        stable = oracle.stable_logaddexp_f32(a, b)
        reference = oracle.reference_logaddexp(a, b)
        assert math.isfinite(stable)
        assert abs(stable - reference) <= _f32_tolerance(reference)


def test_reported_negative_failure_is_reproduced_by_ftz_intermediate_model():
    assert oracle.naive_logaddexp_f32(-100.0, -100.0) == -math.inf
    assert math.isfinite(
        oracle.naive_logaddexp_f32(-100.0, -100.0, flush_subnormals=False)
    )
    stable = oracle.stable_logaddexp_f32(-100.0, -100.0)
    assert stable == oracle.fp32(-100.0 + math.log(2.0))
    assert math.isfinite(stable)


def test_logaddexp_boundary_rows_preserve_working_range():
    assert oracle.naive_logaddexp_f32(88.0, 88.0) == oracle.stable_logaddexp_f32(88.0, 88.0)
    assert oracle.naive_logaddexp_f32(5.0, 3.0) == oracle.stable_logaddexp_f32(5.0, 3.0)
    assert oracle.naive_logaddexp_f32(88.7, 0.0) == oracle.stable_logaddexp_f32(88.7, 0.0)
    assert math.isinf(oracle.naive_logaddexp_f32(89.0, 0.0))


def test_logaddexp2_reported_thresholds():
    assert oracle.naive_logaddexp2_f32(126.0, 126.0) == 127.0
    assert math.isinf(oracle.naive_logaddexp2_f32(128.0, 128.0))
    assert oracle.naive_logaddexp2_f32(-126.0, -126.0) == -125.0
    assert oracle.naive_logaddexp2_f32(-128.0, -128.0) == -math.inf
    assert oracle.stable_logaddexp2_f32(128.0, 128.0) == 129.0
    assert oracle.stable_logaddexp2_f32(-128.0, -128.0) == -127.0


def test_finite_input_bound_and_f32_accuracy_logaddexp():
    values = [-10000.0, -1000.0, -100.0, -89.0, -88.7, -5.0, 0.0, 5.0, 88.0, 89.0, 100.0, 1000.0, 10000.0]
    for a in values:
        for b in values:
            reference = oracle.reference_logaddexp(a, b)
            lower = max(oracle.fp32(a), oracle.fp32(b))
            assert lower <= reference <= lower + math.log(2.0)
            stable = oracle.stable_logaddexp_f32(a, b)
            assert math.isfinite(stable)
            assert abs(stable - reference) <= _f32_tolerance(reference)


def test_finite_input_bound_and_f32_accuracy_logaddexp2():
    values = [-10000.0, -1000.0, -128.0, -126.0, -5.0, 0.0, 5.0, 126.0, 128.0, 1000.0, 10000.0]
    for a in values:
        for b in values:
            reference = oracle.reference_logaddexp2(a, b)
            lower = max(oracle.fp32(a), oracle.fp32(b))
            assert lower <= reference <= lower + 1.0
            stable = oracle.stable_logaddexp2_f32(a, b)
            assert math.isfinite(stable)
            assert abs(stable - reference) <= _f32_tolerance(reference)


def test_symmetry_is_exact_after_input_and_output_rounding():
    values = [-10000.0, -100.0, -1.25, 0.0, 1.25, 100.0, 10000.0]
    for a in values:
        for b in values:
            assert oracle.stable_logaddexp_f32(a, b) == oracle.stable_logaddexp_f32(b, a)
            assert oracle.stable_logaddexp2_f32(a, b) == oracle.stable_logaddexp2_f32(b, a)


def test_currently_working_range_remains_close_to_naive_f32():
    values = [-40.0, -5.0, -1.0, 0.0, 1.0, 5.0, 40.0]
    for a in values:
        for b in values:
            naive = oracle.naive_logaddexp_f32(a, b)
            stable = oracle.stable_logaddexp_f32(a, b)
            assert math.isfinite(naive)
            assert abs(naive - stable) <= 5e-5

            naive2 = oracle.naive_logaddexp2_f32(a, b)
            stable2 = oracle.stable_logaddexp2_f32(a, b)
            assert math.isfinite(naive2)
            assert abs(naive2 - stable2) <= 5e-5


def test_large_equal_inputs_stay_finite_and_return_expected_correction():
    assert oracle.stable_logaddexp_f32(10000.0, 10000.0) == oracle.fp32(10000.0 + math.log(2.0))
    assert oracle.stable_logaddexp_f32(-10000.0, -10000.0) == oracle.fp32(-10000.0 + math.log(2.0))
    assert oracle.stable_logaddexp2_f32(10000.0, 10000.0) == 10001.0
    assert oracle.stable_logaddexp2_f32(-10000.0, -10000.0) == -9999.0


def test_report_serializes_nonfinite_naive_rows_explicitly():
    rows = oracle.report()
    assert rows
    lookup = {(row["op"], row["a"], row["b"]): row for row in rows}
    assert lookup[("logaddexp", 100.0, 0.0)]["naive"] == "inf"
    assert lookup[("logaddexp", -100.0, -100.0)]["naive"] == "-inf"
    assert lookup[("logaddexp2", 128.0, 128.0)]["naive"] == "inf"
    assert lookup[("logaddexp2", -128.0, -128.0)]["naive"] == "-inf"


def test_nonfinite_inputs_fail_closed():
    for value in [math.inf, -math.inf, math.nan]:
        try:
            oracle.stable_logaddexp_f32(value, 0.0)
        except ValueError:
            pass
        else:
            raise AssertionError("nonfinite input must be rejected")
