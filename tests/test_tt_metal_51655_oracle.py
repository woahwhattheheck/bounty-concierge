import importlib.util
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "work"
    / "high-value"
    / "tt-metal-51655"
    / "oracle.py"
)
SPEC = importlib.util.spec_from_file_location("tt_metal_51655_oracle", MODULE_PATH)
oracle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oracle)


def test_source_and_carrier_pins_are_exact():
    assert oracle.TT_METAL_MAIN_SHA == "708e7f58aef9d089f0e5097d4a772c7c8ee129ea"
    assert oracle.CARRIER_PR == 53639
    assert oracle.CARRIER_HEAD_SHA == "11c41b499c36ea55624226cf0fb3b665cdc0780a"
    assert oracle.SOURCE_BLOBS["blackhole_typecast"] == "d78ce8c9fa922f7b9491070eb8af393917814458"
    assert oracle.SOURCE_BLOBS["wormhole_typecast"] == "bb5a5848dad46bfc5b6b40c27f27cc4362741f26"


def test_filed_reproducer_contract_is_cross_dtype_truncation():
    expected = [0, 1, 2, 3, 4, 0, 0]
    rows = oracle.filed_reproducer()
    assert rows == {
        "uint8": expected,
        "uint16": expected,
        "uint32": expected,
        "int32": expected,
    }


def test_legacy_uint16_rounding_disagrees_on_fractional_values():
    assert oracle.legacy_uint16_device(0.5) == 1
    assert oracle.truncation_contract(0.5, "uint16") == 0
    assert oracle.legacy_uint16_device(2.5) == 3
    assert oracle.truncation_contract(2.5, "uint16") == 2


def test_all_bfloat16_patterns_reproduce_filed_576_disagreements():
    in_range, disagreements = oracle.bf16_uint16_legacy_divergences()
    assert in_range == 18305
    assert disagreements == 576


def test_unsigned_contract_clamps_negative_then_truncates():
    for dtype in ("uint8", "uint16", "uint32"):
        assert oracle.truncation_contract(-1.75, dtype) == 0
        assert oracle.truncation_contract(0.75, dtype) == 0
        assert oracle.truncation_contract(1.75, dtype) == 1


def test_signed_contract_truncates_toward_zero_on_both_sides():
    assert oracle.truncation_contract(-1.75, "int32") == -1
    assert oracle.truncation_contract(-0.75, "int32") == 0
    assert oracle.truncation_contract(0.75, "int32") == 0
    assert oracle.truncation_contract(1.75, "int32") == 1


def test_blackhole_residual_encodings_are_reachable_float32_counterexamples():
    rows = oracle.residual_contract_rows()
    assert [row["bits"] for row in rows] == [
        "0x3F7FFFFE",
        "0x3F7FFFFF",
        "0x3FFFFFFF",
    ]
    assert [row["expected_trunc"] for row in rows] == [0, 0, 1]
    assert [row["value"] for row in rows] == [
        0.9999998807907104,
        0.9999999403953552,
        1.9999998807907104,
    ]


def test_carrier_quarter_step_suite_cannot_hit_blackhole_residual_encodings():
    stimulus = oracle.carrier_fractional_stimulus_bits()
    assert len(stimulus) == 189
    assert oracle.float32_bits(0.5) in stimulus
    assert oracle.float32_bits(2.5) in stimulus
    assert set(oracle.BLACKHOLE_RND_ZERO_RESIDUAL_BITS).isdisjoint(stimulus)


def test_verify_summary_is_self_consistent():
    summary = oracle.verify()
    assert summary["status"] == "PASS"
    assert summary["bf16_uint16_legacy_disagreements"] == 576
    assert summary["carrier_quarter_step_covers_any_residual"] is False
