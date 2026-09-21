import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "concierge" / "tt_metal_55130_cache_gate.py"
SPEC = importlib.util.spec_from_file_location("tt_metal_55130_cache_gate", MODULE_PATH)
gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(gate)


CLASSIC = "ttnn/cpp/ttnn/operations/eltwise"
QUASAR = "ttnn/cpp/ttnn/operations/experimental/quasar"


def _write(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _seed_tree(root, prefix):
    _write(
        root,
        f"{prefix}/binary/common/binary_op_types.hpp",
        """
        struct BiasGeluParams {
            bool fast_and_approximate = false;
        };
        using BinaryOpParams = std::variant<BiasGeluParams>;
        """,
    )
    _write(
        root,
        f"{prefix}/binary_ng/device/binary_ng_device_operation.hpp",
        """
        static constexpr auto attribute_names = std::make_tuple("binary_op_type", "op_params");
        auto attribute_values() const {
            return std::make_tuple(
                binary_op_type,
                binary_op_type == BinaryOpType::BIAS_GELU
                    ? op_params
                    : std::optional<binary::BinaryOpParams>{});
        }
        """,
    )
    _write(
        root,
        f"{prefix}/binary_ng/device/binary_ng_device_operation.cpp",
        """
        if (binary_op_type == BinaryOpType::BIAS_GELU) {
            operation_attributes.op_params =
                binary::BiasGeluParams{.fast_and_approximate = fast_and_approximate_mode.value_or(false)};
        }
        // second construction models the tensor/scalar overload
        if (binary_op_type == BinaryOpType::BIAS_GELU) {
            operation_attributes.op_params =
                binary::BiasGeluParams{.fast_and_approximate = fast_and_approximate_mode.value_or(false)};
        }
        """,
    )
    _write(
        root,
        f"{prefix}/binary_ng/device/binary_ng_utils.cpp",
        """
        switch (binary_op_type) {
        case BinaryOpType::BIAS_GELU: {
            const auto* gelu_params =
                op_params.has_value() ? std::get_if<binary::BiasGeluParams>(&op_params.value()) : nullptr;
            const bool fast_and_approximate = gelu_params != nullptr && gelu_params->fast_and_approximate;
            postprocess = unary::EltwiseUnaryWithParam{
                unary::UnaryOpType::GELU, fast_and_approximate ? 1.0f : 0.0f};
            break;
        }
        }
        """,
    )
    _write(
        root,
        f"{prefix}/binary_ng/device/binary_ng_program_factory.cpp",
        """
        const auto& op_params = operation_attributes.op_params;
        const auto op_config =
            OpConfig(op_type, std::in_place_type<OpConfig::FpuBinaryOp>, a_dtype, op_params);
        """,
    )


def _seed_cache_test(root):
    _write(
        root,
        gate.TEST_PATH,
        """
def test_bias_gelu_program_cache_mode_isolation(device, ttnn, x, b):
    device.enable_program_cache()
    device.clear_program_cache()
    default = ttnn.bias_gelu(x, b)
    explicit_false = ttnn.bias_gelu(x, b, fast_and_approximate_mode=False)
    exact_entries = device.num_program_cache_entries()
    explicit_true = ttnn.bias_gelu(x, b, fast_and_approximate_mode=True)
    fast_entries = device.num_program_cache_entries()
    assert fast_entries == exact_entries + 1
    assert default is not None and explicit_false is not None and explicit_true is not None
""",
    )


@pytest.fixture
def good_root(tmp_path):
    _seed_tree(tmp_path, CLASSIC)
    _seed_tree(tmp_path, QUASAR)
    _seed_cache_test(tmp_path)
    return tmp_path


def test_good_fixture_passes(good_root):
    report = gate.evaluate(good_root)
    assert report["verdict"] == "PASS"
    assert report["classic_quasar_check_symmetry"] is True
    assert report["program_cache_regression"]["pass"] is True


def test_missing_cache_regression_holds(good_root):
    _write(
        good_root,
        gate.TEST_PATH,
        """
def test_bias_gelu_numerics(device, ttnn, x, b):
    return ttnn.bias_gelu(x, b, fast_and_approximate_mode=True)
""",
    )
    report = gate.evaluate(good_root)
    assert report["verdict"] == "HOLD"
    assert report["program_cache_regression"]["pass"] is False


def test_single_mode_population_is_rejected(good_root):
    path = good_root / CLASSIC / "binary_ng/device/binary_ng_device_operation.cpp"
    text = path.read_text(encoding="utf-8")
    marker = "// second construction models the tensor/scalar overload"
    second = text.index(marker)
    first_tail = text.index("}", second)
    path.write_text(text[:second] + text[first_tail + 1 :], encoding="utf-8")
    report = gate.evaluate(good_root)
    classic = next(item for item in report["trees"] if item["name"] == "classic")
    assert classic["mode_population_count"] == 1
    assert classic["checks"]["tensor_and_scalar_populate_mode"] is False
    assert report["verdict"] == "HOLD"


def test_cache_key_must_include_mode_params(good_root):
    path = good_root / QUASAR / "binary_ng/device/binary_ng_device_operation.hpp"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        'binary_op_type == BinaryOpType::BIAS_GELU\n                    ? op_params\n                    : std::optional<binary::BinaryOpParams>{}',
        "std::optional<binary::BinaryOpParams>{}",
    )
    path.write_text(text, encoding="utf-8")
    report = gate.evaluate(good_root)
    quasar = next(item for item in report["trees"] if item["name"] == "quasar")
    assert quasar["checks"]["cache_value_bias_gelu_scoped"] is False
    assert report["verdict"] == "HOLD"


def test_codegen_must_use_mode_parameter(good_root):
    path = good_root / CLASSIC / "binary_ng/device/binary_ng_utils.cpp"
    text = path.read_text(encoding="utf-8").replace(
        "fast_and_approximate ? 1.0f : 0.0f",
        "0.0f",
    )
    path.write_text(text, encoding="utf-8")
    report = gate.evaluate(good_root)
    classic = next(item for item in report["trees"] if item["name"] == "classic")
    assert classic["checks"]["gelu_codegen_is_parametrized"] is False


def test_comment_decoys_do_not_satisfy_gate(good_root):
    path = good_root / CLASSIC / "binary/common/binary_op_types.hpp"
    path.write_text(
        """
        // struct BiasGeluParams { bool fast_and_approximate = false; };
        // using BinaryOpParams = std::variant<BiasGeluParams>;
        using BinaryOpParams = std::variant<int>;
        """,
        encoding="utf-8",
    )
    report = gate.evaluate(good_root)
    classic = next(item for item in report["trees"] if item["name"] == "classic")
    assert classic["checks"]["bias_params_default_false"] is False
    assert classic["checks"]["params_variant_contains_bias_gelu"] is False


def test_default_false_is_part_of_contract(good_root):
    path = good_root / QUASAR / "binary/common/binary_op_types.hpp"
    text = path.read_text(encoding="utf-8").replace(
        "bool fast_and_approximate = false;",
        "bool fast_and_approximate = true;",
    )
    path.write_text(text, encoding="utf-8")
    report = gate.evaluate(good_root)
    quasar = next(item for item in report["trees"] if item["name"] == "quasar")
    assert quasar["checks"]["bias_params_default_false"] is False
    assert report["verdict"] == "HOLD"


def test_default_false_and_true_must_share_one_cache_lifetime(good_root):
    _write(
        good_root,
        gate.TEST_PATH,
        """
def test_bias_gelu_default_only(device, ttnn, x, b):
    device.enable_program_cache()
    device.clear_program_cache()
    ttnn.bias_gelu(x, b)
    ttnn.bias_gelu(x, b, fast_and_approximate_mode=True)
    assert device.num_program_cache_entries() > 0
""",
    )
    report = gate.evaluate(good_root)
    assert report["program_cache_regression"]["pass"] is False
    assert report["verdict"] == "HOLD"
