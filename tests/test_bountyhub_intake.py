"""Focused offline checks for public catalog funding, custody and projection."""
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

SPEC = importlib.util.spec_from_file_location("bountyhub_intake", Path(__file__).parents[1] / "tools/bountyhub_intake.py")
intake = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(intake)


def row(**overrides):
    value = {
        "id": "fe773082-b67f-4af3-a5e6-7011ddd18dcd", "title": "Example",
        "repositoryFullName": "owner/repo", "issueNumber": 1, "totalAmount": "15.00",
        "paymentStatus": "PROMISED", "assignmentType": "open", "assignee": None,
        "issueState": "open", "claimed": False, "solved": False,
        "retracted": False, "isFrozen": False, "deletedAt": None,
    }
    value.update(overrides)
    return value


def project(*rows, **envelope):
    return intake.normalize(json.dumps({"data": list(rows), "hasNextPage": False, **envelope}).encode(), intake.money("15"))


class BountyHubIntakeTest(unittest.TestCase):
    def test_inclusive_minimum_and_promised_not_excluded(self):
        result = project(row(), row(totalAmount="14.99"), row(totalAmount="15.01", paymentStatus="PAID", amountPaid="99.99"))
        self.assertEqual([r["catalog_candidate"] for r in result["rows"]], [True, False, True])
        self.assertEqual(result["rows"][2]["advertised_usd"], "15.01")
        for invalid in (True, "NaN", "Infinity", -1, "15.001"):
            with self.subTest(invalid=invalid), self.assertRaises(intake.IntakeError):
                intake.money(invalid)

    def test_claimed_false_does_not_erase_exclusive_assignee(self):
        result = project(row(assignmentType="exclusive", assignee={"username": "gambhirsharma"}, claimed=False))
        self.assertFalse(result["rows"][0]["catalog_candidate"])
        self.assertEqual(result["rows"][0]["reconciliation_reasons"], ["assigned"])
        self.assertEqual(result["rows"][0]["assignee_username"], "gambhirsharma")

    def test_billing_and_unknown_nested_fields_are_not_emitted(self):
        result = project(row(stripeSessionId="billing-secret", paypalOrderId="paypal-secret", amountPaid="999", body="body-secret", assignee={"username": "person", "lastName": "private-name", "paymentVerified": "private-state"}))
        serialized = json.dumps(result)
        for forbidden in ("stripeSessionId", "billing-secret", "paypal", "amountPaid", "body-secret", "lastName", "private-name", "private-state"):
            self.assertNotIn(forbidden, serialized)

    def test_same_issue_cards_share_work_key_without_aggregation(self):
        result = project(row(totalAmount="250.00", repositoryFullName="MicroG/GmsCore", issueNumber=580), row(id="ef91cb1e-dd69-4a33-bd90-21c9679c9247", totalAmount="150.00", repositoryFullName="microg/gmscore", issueNumber=580))
        self.assertEqual(result["distinct_issue_count"], 1)
        self.assertEqual(len(result["duplicate_work_keys"]["github:microg/gmscore#580"]), 2)
        self.assertEqual([r["advertised_usd"] for r in result["rows"]], ["250.00", "150.00"])
        self.assertNotIn("400.00", json.dumps(result))

    def test_unknown_and_malformed_rows_remain_visible(self):
        unknown = row(claimed="false", paymentStatus={"unexpected": "nested"}, assignmentType=[])
        del unknown["assignee"]
        result = project(None, unknown, hasNextPage="false")
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(result["catalog_candidate_count"], 0)
        self.assertIsNone(result["has_next_page"])
        self.assertIsNone(result["retrieved_at"])
        self.assertIn("unknown_claimed", result["rows"][1]["reconciliation_reasons"])
        self.assertIn("unknown_assignee", result["rows"][1]["reconciliation_reasons"])
        self.assertIn("unknown_funding_status", result["rows"][1]["reconciliation_reasons"])

    def test_fetch_is_single_request_and_reports_retry_after(self):
        error = HTTPError(intake.API_URL, 429, "rate limit", {"Retry-After": "60"}, io.BytesIO(b"not-for-output"))
        with patch.object(intake, "urlopen", side_effect=error) as fetch:
            with self.assertRaisesRegex(intake.IntakeError, "HTTP 429; Retry-After=60; no retry"):
                intake.fetch_page(intake.catalog_url(1, 50))
            self.assertEqual(fetch.call_count, 1)
            headers = fetch.call_args.args[0].headers
            self.assertNotIn("Authorization", headers)


if __name__ == "__main__":
    unittest.main()
