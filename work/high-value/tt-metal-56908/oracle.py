"""Pure-host acceptance oracle for tenstorrent/tt-metal#56908.

This models row-major tile ownership only. It does not emulate Tenstorrent
hardware, establish bounty ownership, or substitute for the issue's required
Wormhole hardware regression.
"""
from dataclasses import dataclass
import json

TT_METAL_MAIN_SHA = "708e7f58aef9d089f0e5097d4a772c7c8ee129ea"

SOURCE_BLOBS = {
    "pre_factory": "f3792b73baafafc69f46181a97bbb6d936e87084",
    "post_factory": "d742534049b8daa8729479803dead802c28c5b0b",
    "welford_factory": "3c03d4f4404bed739864984eadb7a42e580cd38c",
    "pre_reader": "2c1dfefcb0ddae83fe7634f3c01db063ee0f94e0",
    "post_reader": "bb08640b6709887671f66897769179c19f667485",
    "post_writer": "4e7b6424165da5b2b7a20e2de31cbf56b0fc9b1d",
}

PR_HEADS = {
    56983: "e2e2e3a822a007870b4f69976a59369b382a5308",
    57039: "f20fe66791c5a12e0222c3e815fa26b72f6921fe",
    57084: "758857511117c78c334cf1a96d4710d2507ed050",
}

# Exact-head source-diff coverage. True means the reviewed diff contains the
# named mechanism/test surface; this is not a merge recommendation.
PR_COVERAGE = {
    56983: {
        "fixes_input_base_offset": True,
        "fixes_pre_output_base_offset": True,
        "fixes_post_stats_base_offset": True,
        "pre_reader_row_stride": True,
        "post_reader_row_stride": True,
        "post_writer_row_stride": False,
        "welford_local_Wt": False,
        "welford_local_cb_length": False,
        "welford_local_block_size": False,
        "rmsnorm_multirow_regression": True,
        "layernorm_welford_multirow_regression": False,
        "wormhole_hardware_evidence_in_pr_body": False,
    },
    57039: {
        "fixes_input_base_offset": True,
        "fixes_pre_output_base_offset": True,
        "fixes_post_stats_base_offset": True,
        "pre_reader_row_stride": True,
        "post_reader_row_stride": True,
        "post_writer_row_stride": True,
        "welford_local_Wt": True,
        "welford_local_cb_length": True,
        "welford_local_block_size": True,
        "rmsnorm_multirow_regression": True,
        "layernorm_welford_multirow_regression": True,
        "wormhole_hardware_evidence_in_pr_body": False,
    },
    57084: {
        "fixes_input_base_offset": False,
        "fixes_pre_output_base_offset": False,
        "fixes_post_stats_base_offset": False,
        "pre_reader_row_stride": True,
        "post_reader_row_stride": True,
        "post_writer_row_stride": False,
        "welford_local_Wt": True,
        "welford_local_cb_length": False,
        "welford_local_block_size": False,
        "rmsnorm_multirow_regression": False,
        "layernorm_welford_multirow_regression": False,
        "wormhole_hardware_evidence_in_pr_body": False,
    },
}


@dataclass(frozen=True)
class GridCase:
    cores_x: int
    cores_y: int
    tiles_per_core_x: int
    tiles_per_core_y: int

    @property
    def wt_full(self):
        return self.cores_y * self.tiles_per_core_y


CASES = {
    "single_row_single_col": GridCase(8, 1, 1, 8),
    "multirow_single_col": GridCase(4, 1, 4, 8),
    "multirow_two_col": GridCase(4, 2, 4, 8),
    "multirow_four_col": GridCase(4, 4, 8, 8),
}


def expected_owned_pages(case, x, y):
    base = x * case.tiles_per_core_x * case.wt_full + y * case.tiles_per_core_y
    return [
        base + row * case.wt_full + col
        for row in range(case.tiles_per_core_x)
        for col in range(case.tiles_per_core_y)
    ]


def original_flat_pages(case, x, y):
    base = x * case.wt_full + y * case.tiles_per_core_y
    count = case.tiles_per_core_x * case.tiles_per_core_y
    return list(range(base, base + count))


def base_fixed_flat_pages(case, x, y):
    base = x * case.tiles_per_core_x * case.wt_full + y * case.tiles_per_core_y
    count = case.tiles_per_core_x * case.tiles_per_core_y
    return list(range(base, base + count))


def old_base_strided_pages(case, x, y):
    base = x * case.wt_full + y * case.tiles_per_core_y
    return [
        base + row * case.wt_full + col
        for row in range(case.tiles_per_core_x)
        for col in range(case.tiles_per_core_y)
    ]


def full_fixed_pages(case, x, y):
    return expected_owned_pages(case, x, y)


def mismatch_count(case, page_fn):
    mismatches = total = bad_cores = 0
    for x in range(case.cores_x):
        for y in range(case.cores_y):
            expected = expected_owned_pages(case, x, y)
            actual = page_fn(case, x, y)
            if len(actual) != len(expected):
                raise AssertionError("one modeled page per expected page is required")
            here = sum(a != b for a, b in zip(actual, expected))
            mismatches += here
            total += len(expected)
            bad_cores += int(bool(here))
    return {
        "mismatches": mismatches,
        "total": total,
        "bad_cores": bad_cores,
        "cores": case.cores_x * case.cores_y,
    }


def expected_pre_output_base(x, tiles_per_core_x, out0_tiles):
    return x * tiles_per_core_x * out0_tiles


def old_pre_output_base(x, out0_tiles):
    return x * out0_tiles


def expected_post_stats_base(x, tiles_per_core_x, stats_tiles_cols):
    return x * tiles_per_core_x * stats_tiles_cols


def old_post_stats_base(x, stats_tiles_cols):
    return x * stats_tiles_cols


def welford_full_width_pages(case, x, y):
    """Pages requested when Welford reader Wt is full width, not local width."""
    base = x * case.tiles_per_core_x * case.wt_full + y * case.tiles_per_core_y
    read_width = case.wt_full
    row_stride = case.wt_full - case.tiles_per_core_y
    pages = []
    idx = base
    for _ in range(case.tiles_per_core_x):
        pages.extend(range(idx, idx + read_width))
        idx += read_width + row_stride
    return pages


def welford_foreign_page_count(case, x, y):
    expected = set(expected_owned_pages(case, x, y))
    return sum(page not in expected for page in welford_full_width_pages(case, x, y))


def verify():
    census = {}
    for name, case in CASES.items():
        census[name] = {
            "original": mismatch_count(case, original_flat_pages),
            "base_fixed_flat": mismatch_count(case, base_fixed_flat_pages),
            "old_base_strided": mismatch_count(case, old_base_strided_pages),
            "full_fixed": mismatch_count(case, full_fixed_pages),
        }

    assert census["single_row_single_col"]["original"]["mismatches"] == 0
    assert census["multirow_single_col"]["original"]["mismatches"] == 96
    assert census["multirow_single_col"]["old_base_strided"]["mismatches"] == 96
    assert census["multirow_single_col"]["base_fixed_flat"]["mismatches"] == 0

    assert census["multirow_two_col"]["original"]["mismatches"] == 240
    assert census["multirow_two_col"]["base_fixed_flat"]["mismatches"] == 192
    assert census["multirow_two_col"]["old_base_strided"]["mismatches"] == 192
    assert census["multirow_two_col"]["full_fixed"]["mismatches"] == 0

    assert census["multirow_four_col"]["original"]["mismatches"] == 992
    assert census["multirow_four_col"]["base_fixed_flat"]["mismatches"] == 896
    assert census["multirow_four_col"]["old_base_strided"]["mismatches"] == 768
    assert census["multirow_four_col"]["full_fixed"]["mismatches"] == 0

    assert welford_foreign_page_count(CASES["multirow_two_col"], 0, 0) == 40
    assert old_pre_output_base(1, 3) == 3
    assert expected_pre_output_base(1, 4, 3) == 12
    assert old_post_stats_base(2, 2) == 4
    assert expected_post_stats_base(2, 4, 2) == 16

    return {
        "tt_metal_main": TT_METAL_MAIN_SHA,
        "source_blobs": SOURCE_BLOBS,
        "pr_heads": PR_HEADS,
        "census": census,
        "welford_foreign_pages_case_c_x0_y0": 40,
        "status": "PASS",
    }


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2, sort_keys=True))
