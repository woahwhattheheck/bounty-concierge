from copy import deepcopy
import unittest

from concierge.fleet_economic_admission import (
    EconomicAdmissionInputError,
    compile_fleet_economic_admission,
    verify_receipt,
)


POLICY = {
    "schema": "fleet-economic-policy/v1",
    "min_batch_items": 3,
    "max_batch_items": 200,
    "currencies": {
        "RTC": {
            "min_single_reward": "200",
            "min_batch_reward": "500",
            "min_reward_per_agent_hour": "300",
        }
    },
}


def candidate(work_id, source, *, reward="200", effort="0.5", batch_key=None):
    return {
        "work_id": work_id,
        "canonical_source_url": source,
        "currency": "RTC",
        "advertised_reward": reward,
        "estimated_agent_hours": effort,
        "batch_key": batch_key,
    }


def request(*items):
    return {
        "schema": "fleet-economic-admission/v1",
        "policy": deepcopy(POLICY),
        "candidates": list(items),
    }


class FleetEconomicSourceIdentityTests(unittest.TestCase):
    def test_github_issue_aliases_cannot_inflate_batch(self):
        aliases = [
            "https://github.com/Owner/Repo/issues/42",
            "HTTPS://GITHUB.COM/owner/repo/issues/42/",
            "https://www.github.com/OWNER/REPO/issues/042",
        ]
        payload = request(*[
            candidate(
                f"alias-{index}",
                source,
                reward="170",
                effort="0.1",
                batch_key="same-mechanical-batch",
            )
            for index, source in enumerate(aliases)
        ])

        with self.assertRaisesRegex(
            EconomicAdmissionInputError,
            "duplicate canonical_source_url identity",
        ):
            compile_fleet_economic_admission(payload)

    def test_generic_host_case_aliases_fail_closed(self):
        payload = request(
            candidate("one", "https://Example.test/work/1"),
            candidate("two", "HTTPS://example.TEST/work/1"),
        )
        with self.assertRaisesRegex(
            EconomicAdmissionInputError,
            "duplicate canonical_source_url identity",
        ):
            compile_fleet_economic_admission(payload)

    def test_distinct_issue_numbers_remain_distinct_and_receipt_is_bound(self):
        payload = request(
            candidate("issue-42", "https://github.com/Owner/Repo/issues/42"),
            candidate("issue-43", "https://github.com/owner/repo/issues/43"),
        )
        first = compile_fleet_economic_admission(payload)
        second = compile_fleet_economic_admission(deepcopy(payload))

        self.assertEqual(first, second)
        self.assertTrue(verify_receipt(first))
        self.assertRegex(first["source_identity_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(
            first["authority"]["canonical_source_identity_rechecked"]
        )
        self.assertFalse(first["authority"]["dispatch_authority"])
        self.assertEqual(first["economically_eligible_count"], 2)

    def test_noncanonical_source_alias_surfaces_are_rejected(self):
        invalid = [
            "http://github.com/o/r/issues/1",
            "https://user:secret@github.com/o/r/issues/1",
            "https://github.com:443/o/r/issues/1",
            "https://github.com/o/r/issues/1?duplicate=1",
            "https://github.com/o/r/issues/1#duplicate",
            "https://github.com/o/r/issues/%31",
            "https://github.com/o/r/issues/1/../1",
            "https://github.com/o//r/issues/1",
        ]
        for index, source in enumerate(invalid):
            with self.subTest(source=source):
                with self.assertRaises(EconomicAdmissionInputError):
                    compile_fleet_economic_admission(
                        request(candidate(f"bad-{index}", source))
                    )

    def test_distinct_generic_paths_are_not_guessed_equivalent(self):
        receipt = compile_fleet_economic_admission(
            request(
                candidate("plain", "https://example.test/work/1"),
                candidate("slash", "https://example.test/work/1/"),
            )
        )
        self.assertTrue(verify_receipt(receipt))
        self.assertEqual(receipt["economically_eligible_count"], 2)

    def test_source_identity_marker_is_tamper_evident(self):
        receipt = compile_fleet_economic_admission(
            request(candidate("one", "https://github.com/o/r/issues/1"))
        )
        changed = deepcopy(receipt)
        changed["authority"]["canonical_source_identity_rechecked"] = False
        self.assertFalse(verify_receipt(changed))

        changed = deepcopy(receipt)
        changed["source_identity_sha256"] = "0" * 64
        self.assertFalse(verify_receipt(changed))


if __name__ == "__main__":
    unittest.main()
