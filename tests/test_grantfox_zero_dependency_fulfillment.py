from copy import deepcopy
import unittest
from unittest.mock import patch

from concierge.grantfox_dependency_fulfillment import (
    compile_grantfox_dependency_fulfillment,
    verify_dependency_fulfillment_receipt,
)
from tests.test_grantfox_dependency_fulfillment import dependency_receipt, request


class GrantFoxZeroDependencyFulfillmentTests(unittest.TestCase):
    def test_explicit_zero_dependencies_need_no_landings(self):
        upstream = dependency_receipt()
        upstream["evidence"]["dependencies"] = []
        with patch(
            "concierge.grantfox_dependency_fulfillment.verify_dependency_readiness_receipt",
            return_value=True,
        ):
            receipt = compile_grantfox_dependency_fulfillment(
                request(dependency_receipt=upstream, landings=[])
            )
            self.assertEqual(
                receipt["fulfillment_disposition"], "DEPENDENCIES_FULFILLED"
            )
            self.assertEqual(receipt["fulfillment_summary"]["dependency_count"], 0)
            self.assertEqual(
                receipt["fulfillment_summary"]["landing_evidence_count"], 0
            )
            self.assertEqual(
                receipt["fulfillment_summary"]["missing_issue_numbers"], []
            )
            self.assertTrue(verify_dependency_fulfillment_receipt(receipt))


if __name__ == "__main__":
    unittest.main()
