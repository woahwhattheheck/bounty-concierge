import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "work" / "high-value" / "tt-metal-56908" / "oracle.py"
SPEC = importlib.util.spec_from_file_location("tt_metal_56908_oracle", MODULE_PATH)
oracle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oracle)


def test_source_and_pr_heads_are_exact():
    assert oracle.TT_METAL_MAIN_SHA == "708e7f58aef9d089f0e5097d4a772c7c8ee129ea"
    assert oracle.SOURCE_BLOBS["post_writer"] == "4e7b6424165da5b2b7a20e2de31cbf56b0fc9b1d"
    assert oracle.PR_HEADS == {
        56983: "e2e2e3a822a007870b4f69976a59369b382a5308",
        57039: "f20fe66791c5a12e0222c3e815fa26b72f6921fe",
        57084: "758857511117c78c334cf1a96d4710d2507ed050",
    }


def test_single_row_single_column_is_fully_masked():
    case = oracle.CASES["single_row_single_col"]
    assert oracle.mismatch_count(case, oracle.original_flat_pages)["mismatches"] == 0


def test_single_column_masks_stride_but_not_old_base():
    case = oracle.CASES["multirow_single_col"]
    assert oracle.mismatch_count(case, oracle.original_flat_pages)["mismatches"] == 96
    assert oracle.mismatch_count(case, oracle.old_base_strided_pages)["mismatches"] == 96
    assert oracle.mismatch_count(case, oracle.base_fixed_flat_pages)["mismatches"] == 0


def test_multicolumn_requires_both_base_and_stride_fixes():
    case = oracle.CASES["multirow_two_col"]
    assert oracle.mismatch_count(case, oracle.original_flat_pages)["mismatches"] == 240
    assert oracle.mismatch_count(case, oracle.base_fixed_flat_pages)["mismatches"] == 192
    assert oracle.mismatch_count(case, oracle.old_base_strided_pages)["mismatches"] == 192
    assert oracle.mismatch_count(case, oracle.full_fixed_pages)["mismatches"] == 0


def test_large_multicolumn_case_distinguishes_partial_fixes():
    case = oracle.CASES["multirow_four_col"]
    assert oracle.mismatch_count(case, oracle.original_flat_pages)["mismatches"] == 992
    assert oracle.mismatch_count(case, oracle.base_fixed_flat_pages)["mismatches"] == 896
    assert oracle.mismatch_count(case, oracle.old_base_strided_pages)["mismatches"] == 768
    assert oracle.mismatch_count(case, oracle.full_fixed_pages)["mismatches"] == 0


def test_pre_output_base_must_scale_by_rows_per_core():
    assert oracle.old_pre_output_base(1, 3) == 3
    assert oracle.expected_pre_output_base(1, 4, 3) == 12


def test_post_stats_base_must_scale_by_rows_per_core():
    assert oracle.old_post_stats_base(2, 2) == 4
    assert oracle.expected_post_stats_base(2, 4, 2) == 16


def test_welford_full_width_reader_consumes_foreign_pages():
    case = oracle.CASES["multirow_two_col"]
    assert len(oracle.expected_owned_pages(case, 0, 0)) == 32
    assert len(oracle.welford_full_width_pages(case, 0, 0)) == 64
    assert oracle.welford_foreign_page_count(case, 0, 0) == 40


def test_pr_56983_diff_still_lacks_writer_stride_and_local_welford_width():
    c = oracle.PR_COVERAGE[56983]
    assert c["fixes_input_base_offset"] and c["pre_reader_row_stride"] and c["post_reader_row_stride"]
    assert not c["post_writer_row_stride"]
    assert not c["welford_local_Wt"]


def test_pr_57039_diff_covers_modeled_surfaces_but_not_hardware_receipt():
    c = oracle.PR_COVERAGE[57039]
    modeled = [
        "fixes_input_base_offset", "fixes_pre_output_base_offset", "fixes_post_stats_base_offset",
        "pre_reader_row_stride", "post_reader_row_stride", "post_writer_row_stride",
        "welford_local_Wt", "welford_local_cb_length", "welford_local_block_size",
        "rmsnorm_multirow_regression", "layernorm_welford_multirow_regression",
    ]
    assert all(c[key] for key in modeled)
    assert not c["wormhole_hardware_evidence_in_pr_body"]


def test_pr_57084_rebase_does_not_cover_old_base_or_writer():
    c = oracle.PR_COVERAGE[57084]
    assert c["pre_reader_row_stride"] and c["post_reader_row_stride"] and c["welford_local_Wt"]
    assert not c["fixes_input_base_offset"]
    assert not c["fixes_pre_output_base_offset"]
    assert not c["fixes_post_stats_base_offset"]
    assert not c["post_writer_row_stride"]


def test_all_reviewed_pr_bodies_lack_required_wormhole_receipt():
    assert all(not c["wormhole_hardware_evidence_in_pr_body"] for c in oracle.PR_COVERAGE.values())


def test_verify_summary_is_self_consistent():
    summary = oracle.verify()
    assert summary["status"] == "PASS"
    assert summary["census"]["multirow_two_col"]["full_fixed"]["mismatches"] == 0
    assert summary["census"]["multirow_four_col"]["original"]["mismatches"] == 992
