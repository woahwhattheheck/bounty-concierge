import copy
import json
import unittest
from pathlib import Path
import importlib.util


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("verify_acceptance", HERE / "verify_acceptance.py")
verify = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(verify)


class AcceptanceGateTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((HERE / "acceptance.json").read_text())

    def test_snapshot_holds_source_and_hardware(self):
        result = verify.assess(copy.deepcopy(self.data))
        self.assertEqual(result["status"], "HOLD_SOURCE_INTEGRATION_AND_HARDWARE")
        self.assertEqual(len(result["source_blockers"]), 1)
        self.assertEqual(len(result["hardware_blockers"]), 3)
        self.assertFalse(result["new_implementation_pr_allowed"])
        self.assertFalse(result["payout_claimed"])

    def test_integrated_source_still_holds_hardware(self):
        data = copy.deepcopy(self.data)
        data["required_source_followups"][0]["state"] = "merged"
        result = verify.assess(data)
        self.assertEqual(result["status"], "HOLD_HARDWARE")
        self.assertEqual(result["source_blockers"], [])
        self.assertEqual(len(result["hardware_blockers"]), 3)

    def test_all_physical_gates_only_reaches_maintainer_review(self):
        data = copy.deepcopy(self.data)
        data["required_source_followups"][0]["state"] = "merged"
        for gate in data["device_gates"]:
            gate["status"] = "pass"
        result = verify.assess(data)
        self.assertEqual(result["status"], "READY_FOR_MAINTAINER_REVIEW")
        self.assertFalse(result["new_implementation_pr_allowed"])
        self.assertFalse(result["payout_claimed"])

    def test_rejects_duplicate_pr_permission(self):
        data = copy.deepcopy(self.data)
        data["routing"]["new_implementation_pr"] = "allowed"
        with self.assertRaisesRegex(verify.EvidenceError, "duplicate implementation"):
            verify.assess(data)

    def test_rejects_unreconciled_bounty_total(self):
        data = copy.deepcopy(self.data)
        data["issue"]["bounty_usd"] = 15000
        with self.assertRaisesRegex(verify.EvidenceError, "reconcile"):
            verify.assess(data)


if __name__ == "__main__":
    unittest.main()
