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
    / "tt-metal-55130"
    / "bias_gelu_oracle.py"
)
SPEC = importlib.util.spec_from_file_location("tt_metal_55130_bias_gelu_oracle", ORACLE_PATH)
assert SPEC is not None and SPEC.loader is not None
oracle = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = oracle
SPEC.loader.exec_module(oracle)


def test_source_and_economic_pins_are_exact():
    assert oracle.UPSTREAM_MAIN == "708e7f58aef9d089f0e5097d4a772c7c8ee129ea"
    assert oracle.ISSUE_NUMBER == 55130
    assert oracle.REWARD_USD == 5000
    assert oracle.ASSIGNEE == "AJ0070"
    assert oracle.SOURCE_BLOBS == {
        "binary_composite.hpp": "401113560eb8d583c0f93925073dfcb4b4e670a9",
        "binary_nanobind.cpp": "db757a5cae9c3c286735f12ddd21f3f7b10372aa",
        "binary_composite_op.cpp": "10e5caf0a69738e89db904b5d91206467ac490b3",
        "binary_ng_device_operation.cpp": "bbf64d54b15ec9ec6fa4e6aaab175a9dac435c6e",
        "binary_ng_device_operation.hpp": "d5eda4d468b0ca9701678f3872543a5299909204",
        "binary_ng_program_factory.cpp": "4cdd7906cd18a7457a1efa2c957daab0a4fffb6e",
    }


def test_exact_host_is_consistent_with_rounded_issue_table_samples():
    for x, expected in oracle.ISSUE_TORCH_REFERENCE.items():
        assert abs(oracle.exact_gelu(x) - expected) < 5e-5


def test_legacy_issue_samples_show_material_accuracy_gap():
    rows = [oracle.vector(x) for x in oracle.LEGACY_OBSERVED]
    assert max(row.legacy_abs_error for row in rows) > 0.008
    assert oracle.vector(-3.0059).legacy_observed == 0.0
    assert oracle.vector(-3.0059).exact_host < -0.0039


def test_exact_bias_gelu_is_compositionally_consistent():
    pairs = [
        (-5.0, 0.0),
        (-3.5059, 0.5),
        (-1.0, 0.5),
        (0.0, 0.5),
        (3.0, 0.5),
        (5.0, -0.5),
    ]
    for a, bias in pairs:
        assert oracle.exact_bias_gelu(a, bias) == oracle.exact_gelu(a + bias)


def test_exact_gelu_basic_invariants():
    assert oracle.exact_gelu(0.0) == 0.0
    for value in (-30.0, -10.0, -5.0, -1.0, 1.0, 5.0, 10.0, 30.0):
        result = oracle.exact_gelu(value)
        assert math.isfinite(result)
        if value < 0:
            assert result <= 0.0
        else:
            assert result >= 0.0


def test_tanh_reference_is_not_mislabeled_as_current_hardware_model():
    tanh_value = oracle.standard_tanh_gelu(-3.0059)
    assert tanh_value != oracle.LEGACY_OBSERVED[-3.0059]
    assert tanh_value < 0.0


def test_report_is_json_serializable_and_authority_limited():
    payload = oracle.report()
    json.dumps(payload, allow_nan=False)
    assert payload["authority"] == "host-reference-only"
    assert payload["max_legacy_abs_error_on_issue_samples"] > 0.008
    assert payload["max_exact_host_abs_error_on_issue_samples"] < 5e-5


def test_nonfinite_inputs_fail_closed():
    for value in (math.inf, -math.inf, math.nan):
        try:
            oracle.exact_gelu(value)
        except ValueError:
            pass
        else:
            raise AssertionError("nonfinite input must be rejected")
