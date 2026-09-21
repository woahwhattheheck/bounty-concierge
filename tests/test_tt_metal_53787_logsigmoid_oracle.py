from __future__ import annotations

import importlib.util
import json
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


def test_source_pin_matches_reviewed_current_main():
    assert oracle.UPSTREAM_MAIN == "708e7f58aef9d089f0e5097d4a772c7c8ee129ea"
    assert oracle.WORMHOLE_HEADER_BLOB == "cdc5ad762c24219187ad8e6babe57a2d261474ff"
    assert oracle.BLACKHOLE_HEADER_BLOB == "e2ca2e3e875ffcd78f1031df5cfdf4de59b9e0a9"
    assert oracle.COMPUTE_KERNEL_BLOB == "5e6522f035fc2856e805997a678dbfa63a7735f4"
    assert oracle.NIGHTLY_SWEEP_BLOB == "4653c5028a3df72684b11d87cbcd3b29bd4e9285"


def test_missing_negative_branch_returns_raw_input_at_boundary():
    current = oracle.current_source_model(-4.0)
    stable = oracle.stable_logsigmoid_f32(-4.0)
    assert current == oracle.fp32(-4.0)
    assert stable < current
    assert abs(current - stable) > 0.018


def test_missing_negative_branch_extends_below_boundary():
    for value in (-4.0001, -5.0, -10.0):
        x = oracle.fp32(value)
        assert oracle.current_source_model(x) == x
        assert oracle.stable_logsigmoid_f32(x) <= x


def test_negative_boundary_has_structural_discontinuity():
    left = oracle.current_source_model(-4.0)
    right = oracle.current_source_model(-3.9999)
    stable_left = oracle.stable_logsigmoid_f32(-4.0)
    stable_right = oracle.stable_logsigmoid_f32(-3.9999)
    assert abs(left - right) > 0.003
    assert abs(stable_left - stable_right) < 0.001


def test_positive_tail_one_term_truncation_is_visible():
    value = 4.0001
    current = oracle.current_source_model(value)
    reference = oracle.reference_logsigmoid(value)
    stable = oracle.stable_logsigmoid_f32(value)
    assert oracle.relative_error(current, reference) > 0.008
    assert oracle.relative_error(stable, reference) < 3e-7


def test_midrange_polynomial_misses_ln2_at_zero():
    current = oracle.current_source_model(0.0)
    reference = oracle.reference_logsigmoid(0.0)
    assert current == oracle.fp32(-oracle.POLY[0])
    assert abs(current - reference) > 7e-4


def test_stable_identity_is_finite_and_fp32_accurate_across_issue_range():
    values = (-30.0, -20.0, -10.0, -5.0, -4.0001, -4.0, -3.9999, -1.0, 0.0, 1.0, 3.9999, 4.0, 4.0001, 5.0, 10.0, 20.0, 30.0)
    for value in values:
        stable = oracle.stable_logsigmoid_f32(value)
        reference = oracle.reference_logsigmoid(value)
        assert math.isfinite(stable)
        assert stable <= 0.0
        assert oracle.relative_error(stable, reference) < 3e-7


def test_legacy_nightly_sweep_cannot_sample_below_missing_branch_boundary():
    assert oracle.legacy_nightly_domain_contains(-4.0)
    assert not oracle.legacy_nightly_domain_contains(-4.0001)
    assert not oracle.legacy_nightly_domain_contains(-30.0)
    assert oracle.legacy_nightly_domain_contains(10.0)
    assert not oracle.legacy_nightly_domain_contains(10.0001)


def test_report_is_json_serializable_and_preserves_boundary_rows():
    rows = oracle.report()
    json.dumps(rows, allow_nan=False)
    lookup = {row["x"]: row for row in rows}
    assert lookup[oracle.fp32(-4.0)]["current_source_model"] == oracle.fp32(-4.0)
    assert lookup[oracle.fp32(4.0001)]["current_relative_error"] > 0.008


def test_nonfinite_inputs_fail_closed():
    for value in (math.inf, -math.inf, math.nan):
        try:
            oracle.current_source_model(value)
        except ValueError:
            pass
        else:
            raise AssertionError("nonfinite input must be rejected")
