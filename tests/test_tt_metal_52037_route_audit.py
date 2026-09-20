from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


AUDIT_PATH = (
    Path(__file__).resolve().parents[1]
    / "work"
    / "high-value"
    / "tt-metal-52037"
    / "route_audit.py"
)
SPEC = importlib.util.spec_from_file_location("tt_metal_52037_route_audit", AUDIT_PATH)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def _legacy_binary_ng() -> str:
    return r"""
switch (binary_op_type) {
case BinaryOpType::LOGADDEXP:
    if (is_sfpu_op()) {
        binary_op = SfpuBinaryOp::LOGADDEXP;
    } else {
        process_lhs = unary::UnaryOpType::EXP;
        process_rhs = unary::UnaryOpType::EXP;
        binary_op = EnumT::ADD;
        postprocess = unary::UnaryOpType::LOG;
    }
    break;
case BinaryOpType::LOGADDEXP2:
    if (is_sfpu_op()) {
        binary_op = SfpuBinaryOp::LOGADDEXP2;
    } else {
        process_lhs = unary::UnaryOpType::EXP2;
        process_rhs = unary::UnaryOpType::EXP2;
        binary_op = EnumT::ADD;
        postprocess = unary::UnaryOpType::LOG2;
    }
    break;
default:
    break;
}
"""


def _legacy_common() -> str:
    return r"""
switch (op_type) {
case BinaryOpType::LOGADDEXP:
    defines.merge(get_defines(UnaryOpType::EXP, {}, "PRE_IN0_0"));
    op_name = "add_tiles";
    defines.merge(get_defines(UnaryOpType::LOG, {}, "0"));
    break;
case BinaryOpType::LOGADDEXP2:
    defines.merge(get_defines(UnaryOpType::EXP2, {}, "PRE_IN0_0"));
    op_name = "add_tiles";
    defines.merge(get_defines(UnaryOpType::LOG2, {}, "0"));
    break;
default:
    break;
}
switch (op_type) {
case BinaryOpType::LOGADDEXP:
    new_defines.merge(get_defines(UnaryOpType::EXP, {}, "PRE_IN0_0"));
    op_name = "add_binary_tile";
    new_defines.merge(get_defines(UnaryOpType::LOG, {}, "0"));
    break;
case BinaryOpType::LOGADDEXP2:
    new_defines.merge(get_defines(UnaryOpType::EXP2, {}, "PRE_IN0_0"));
    op_name = "add_binary_tile";
    new_defines.merge(get_defines(UnaryOpType::LOG2, {}, "0"));
    break;
default:
    break;
}
"""


def _fixed_binary_ng() -> str:
    return r"""
switch (binary_op_type) {
case BinaryOpType::LOGADDEXP:
    binary_op = SfpuBinaryOp::LOGADDEXP;
    break;
case BinaryOpType::LOGADDEXP2:
    binary_op = SfpuBinaryOp::LOGADDEXP2;
    break;
default:
    break;
}
"""


def _fixed_common() -> str:
    return r"""
switch (op_type) {
case BinaryOpType::LOGADDEXP:
    op_name = "stable_logaddexp_tile";
    break;
case BinaryOpType::LOGADDEXP2:
    op_name = "stable_logaddexp2_tile";
    break;
default:
    break;
}
switch (op_type) {
case BinaryOpType::LOGADDEXP:
    op_name = "stable_logaddexp_binary_tile";
    break;
case BinaryOpType::LOGADDEXP2:
    op_name = "stable_logaddexp2_binary_tile";
    break;
default:
    break;
}
"""


def test_current_shape_reports_all_six_legacy_routes():
    result = audit.audit_source_texts(_legacy_binary_ng(), _legacy_common())
    assert result["ok"] is False
    assert result["errors"] == []
    assert result["legacy_route_count"] == 6
    assert {(r["site"], r["op"]) for r in result["routes"] if r["legacy"]} == {
        ("binary_ng", "LOGADDEXP"),
        ("binary_ng", "LOGADDEXP2"),
        ("common_fpu", "LOGADDEXP"),
        ("common_fpu", "LOGADDEXP2"),
        ("common_sfpu", "LOGADDEXP"),
        ("common_sfpu", "LOGADDEXP2"),
    }


def test_fully_replaced_routes_pass_static_completeness():
    result = audit.audit_source_texts(_fixed_binary_ng(), _fixed_common())
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["legacy_route_count"] == 0
    assert len(result["routes"]) == 6


def test_missing_route_fails_closed_instead_of_looking_fixed():
    common = _fixed_common().replace(
        'case BinaryOpType::LOGADDEXP2:\n    op_name = "stable_logaddexp2_binary_tile";\n    break;\n',
        "",
        1,
    )
    result = audit.audit_source_texts(_fixed_binary_ng(), common)
    assert result["ok"] is False
    assert result["legacy_route_count"] == 0
    assert any(
        error == "common:LOGADDEXP2: expected exactly 2 case blocks, found 1"
        for error in result["errors"]
    )


def test_narrow_stable_branch_cannot_hide_legacy_fallback():
    mixed = r"""
switch (binary_op_type) {
case BinaryOpType::LOGADDEXP:
    if (input_a_dtype == input_b_dtype) {
        binary_op = SfpuBinaryOp::LOGADDEXP;
    } else {
        process_lhs = unary::UnaryOpType::EXP;
        process_rhs = unary::UnaryOpType::EXP;
        binary_op = EnumT::ADD;
        postprocess = unary::UnaryOpType::LOG;
    }
    break;
case BinaryOpType::LOGADDEXP2:
    binary_op = SfpuBinaryOp::LOGADDEXP2;
    break;
default:
    break;
}
"""
    result = audit.audit_source_texts(mixed, _fixed_common())
    legacy = [r for r in result["routes"] if r["legacy"]]
    assert result["ok"] is False
    assert [(r["site"], r["op"]) for r in legacy] == [
        ("binary_ng", "LOGADDEXP")
    ]


def test_comment_only_legacy_tokens_do_not_create_false_red():
    binary = _fixed_binary_ng().replace(
        "binary_op = SfpuBinaryOp::LOGADDEXP;",
        """// old: UnaryOpType::EXP + EnumT::ADD + UnaryOpType::LOG
    binary_op = SfpuBinaryOp::LOGADDEXP;""",
        1,
    )
    common = _fixed_common().replace(
        'op_name = "stable_logaddexp_tile";',
        """/* old path used UnaryOpType::EXP, add_tiles, UnaryOpType::LOG */
    op_name = "stable_logaddexp_tile";""",
        1,
    )
    result = audit.audit_source_texts(binary, common)
    assert result["ok"] is True


def test_logaddexp_does_not_match_logaddexp2_prefix():
    blocks = audit.extract_case_blocks(_fixed_binary_ng(), "LOGADDEXP")
    blocks2 = audit.extract_case_blocks(_fixed_binary_ng(), "LOGADDEXP2")
    assert len(blocks) == 1
    assert len(blocks2) == 1
    assert "LOGADDEXP2" not in blocks[0]
    assert "LOGADDEXP2" in blocks2[0]


def test_missing_source_paths_fail_closed(tmp_path: Path):
    result = audit.audit_source_root(tmp_path)
    assert result["ok"] is False
    assert len(result["errors"]) == 2
    assert result["routes"] == []
