import unittest

from work.grantfox.openao_6_pr111.verify_sparse_graphics import (
    analyze,
    current_pr_accepts_as_original,
    false_positive_original_ids,
)


class SparseGraphicOracleTests(unittest.TestCase):
    def test_current_pr_range_accepts_missing_in_range_id(self) -> None:
        self.assertTrue(current_pr_accepts_as_original(5750))

    def test_sparse_catalog_exposes_false_positive(self) -> None:
        catalog = {"5749": [1, 0, 0, 32, 32], "5752": [1, 0, 0, 32, 32]}
        self.assertEqual(false_positive_original_ids(catalog)[0], 1)
        report = analyze(catalog, 5750)
        self.assertTrue(report["probe_false_positive"])

    def test_present_original_is_not_false_positive(self) -> None:
        catalog = {str(i): [1, 0, 0, 32, 32] for i in range(1, 20)}
        report = analyze(catalog, 10)
        self.assertTrue(report["probe_catalog_exists"])
        self.assertFalse(report["probe_false_positive"])


if __name__ == "__main__":
    unittest.main()
