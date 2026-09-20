import unittest

from work.grantfox.openao_24_pr369.verify_isolation_contract import (
    USER_MAP_END,
    USER_MAP_START,
    analyze,
    candidate_exit_accepts,
    candidate_npc_exp_accepts,
    candidate_storage_accepts,
    required_exit_accepts,
    required_npc_exp_accepts,
    required_storage_accepts,
)


class OpenAO24PR369AcceptanceTests(unittest.TestCase):
    def test_aggregate_storage_hostile_differentiates(self) -> None:
        limit = 5 * 1024 * 1024
        existing = 3 * 1024 * 1024
        incoming = 3 * 1024 * 1024
        self.assertTrue(candidate_storage_accepts(limit, existing, incoming))
        self.assertFalse(required_storage_accepts(limit, existing, incoming))

    def test_single_map_inside_account_quota_remains_allowed(self) -> None:
        limit = 5 * 1024 * 1024
        incoming = 3 * 1024 * 1024
        self.assertTrue(required_storage_accepts(limit, 0, incoming))

    def test_positive_npc_exp_hostile_differentiates(self) -> None:
        self.assertTrue(candidate_npc_exp_accepts(1))
        self.assertFalse(required_npc_exp_accepts(1))

    def test_zero_npc_exp_is_allowed(self) -> None:
        self.assertTrue(required_npc_exp_accepts(0))

    def test_user_range_boundaries(self) -> None:
        self.assertTrue(required_exit_accepts(USER_MAP_START))
        self.assertTrue(required_exit_accepts(USER_MAP_END))
        self.assertFalse(required_exit_accepts(USER_MAP_START - 1))
        self.assertFalse(required_exit_accepts(USER_MAP_END + 1))

    def test_candidate_accepts_above_reserved_range(self) -> None:
        self.assertTrue(candidate_exit_accepts(1_000_000))
        self.assertFalse(required_exit_accepts(1_000_000))

    def test_report_contains_three_differentiating_hostiles(self) -> None:
        report = analyze()
        self.assertTrue(report["all_hostiles_differentiate"])
        self.assertEqual(len(report["findings"]), 3)
        self.assertTrue(all(item["differentiates"] for item in report["findings"]))


if __name__ == "__main__":
    unittest.main()
