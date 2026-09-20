from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys


ORACLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "work"
    / "high-value"
    / "tt-metal-53787"
    / "logsigmoid_oracle.py"
)
SPEC = importlib.util.spec_from_file_location("tt_metal_53787_logsigmoid_oracle", ORACLE_PATH)
assert SPEC is not None and SPEC.loader is not None
oracle = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = oracle
SPEC.loader.exec_module(oracle)


def test_current_model_reproduces_missing_negative_branch():
    for x in (-30.0, -10.0, -5.0, -4.0001, -4.0):
        assert oracle.current_source_model_f32(x) == oracle.fp32(x)
        assert oracle.source_regime(x) == "raw-input-missing-negative-branch"


def test_minus_four_boundary_has_material_error_and_large_ulp_gap():
    result = oracle.evaluate(-4.0)
    assert result.current == -4.0
    assert abs(result.current - result.reference) > 0.018
    assert 0.0045 < result.current_relative_error < 0.0046
    assert result.current_ulp_vs_reference_f32 > 38_000
    assert result.stable_ulp_vs_reference_f32 == 0


def test_polynomial_representative_rows_match_checked_in_coefficients():
    expected = {
        -1.0: -1.3061909675598145,
        0.0: -0.6924354434013367,
        1.0: -0.31501784920692444,
        4.0: -0.018150150775909424,
    }
    for x, want in expected.items():
        assert oracle.current_source_model_f32(x) == want
        assert oracle.source_regime(x) == "degree-8-polynomial"


def test_midrange_polynomial_error_is_not_hidden_by_loose_absolute_tolerance():
    zero = oracle.evaluate(0.0)
    one = oracle.evaluate(1.0)
    assert abs(zero.current - zero.reference) > 7e-4
    assert zero.current_ulp_vs_reference_f32 > 10_000
    assert one.current_relative_error > 0.005
    assert one.current_ulp_vs_reference_f32 > 50_000


def test_positive_tail_one_term_error_peaks_just_above_four():
    result = oracle.evaluate(4.0001)
    assert result.source_regime == "one-term-positive-tail"
    assert result.current_relative_error > 0.009
    assert result.current_ulp_vs_reference_f32 > 80_000
    assert result.stable_ulp_vs_reference_f32 == 0


def test_positive_tail_model_uses_accurate_exp_to_isolate_truncation_error():
    result = oracle.evaluate(5.0)
    exact_one_term = oracle.fp32(-math.exp(-oracle.fp32(5.0)))
    assert result.current == exact_one_term
    assert result.current_relative_error > 0.003
    assert result.current_ulp_vs_reference_f32 > 40_000


def test_stable_identity_is_finite_across_required_fp32_range():
    values = (
        -30.0,
        -20.0,
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
        20.0,
        30.0,
    )
    for x in values:
        stable = oracle.stable_logsigmoid_f32(x)
        reference = oracle.reference_logsigmoid(x)
        assert math.isfinite(stable)
        assert stable <= 0.0
        assert oracle.ulp_distance_f32(stable, oracle.fp32(reference)) == 0


def test_stable_identity_has_correct_asymptotic_behavior():
    negative = oracle.stable_logsigmoid_f32(-30.0)
    positive = oracle.stable_logsigmoid_f32(30.0)
    assert negative == oracle.fp32(oracle.reference_logsigmoid(-30.0))
    assert abs(negative + 30.0) <= 2e-6
    assert positive < 0.0
    assert abs(positive) < 1e-12


def test_regime_boundaries_are_explicit_and_non_overlapping():
    assert oracle.source_regime(-4.0) == "raw-input-missing-negative-branch"
    assert oracle.source_regime(-3.9999) == "degree-8-polynomial"
    assert oracle.source_regime(4.0) == "degree-8-polynomial"
    assert oracle.source_regime(4.0001) == "one-term-positive-tail"


def test_default_acceptance_vectors_cover_both_sides_of_each_source_boundary():
    values = set(oracle.default_vectors())
    for value in (-4.0001, -4.0, -3.9999, 3.9999, 4.0, 4.0001):
        assert value in values
    assert min(values) <= -30.0
    assert max(values) >= 30.0


def test_ulp_distance_is_symmetric_and_zero_for_identical_values():
    values = (-30.0, -4.0, -0.0, 0.0, 4.0, 30.0)
    for value in values:
        assert oracle.ulp_distance_f32(value, value) == 0
    assert oracle.ulp_distance_f32(-4.0, -4.0181498527526855) == oracle.ulp_distance_f32(
        -4.0181498527526855, -4.0
    )


def test_nonfinite_inputs_fail_closed():
    for value in (math.inf, -math.inf, math.nan):
        try:
            oracle.evaluate(value)
        except ValueError:
            pass
        else:
            raise AssertionError("nonfinite input must be rejected")
