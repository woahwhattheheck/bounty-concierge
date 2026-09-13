import io
import unittest
from datetime import datetime
from unittest import mock

from concierge import contract_proposal


class ContractProposalTrustedClockTests(unittest.TestCase):
    def test_cli_rejects_caller_as_of_override(self):
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as raised:
                contract_proposal.main([
                    "snapshot.json",
                    "qualification.json",
                    "brief.json",
                    "--as-of",
                    "2026-09-13T10:00:00Z",
                ])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("unrecognized arguments: --as-of", stderr.getvalue())

    def test_cli_forwards_internal_current_utc_to_qualification_builder(self):
        trusted_now = "2026-09-13T13:02:03Z"
        with mock.patch.object(
            contract_proposal, "_load_json", side_effect=[{}, {}, {}]
        ) as load_json, mock.patch.object(
            contract_proposal, "_trusted_current_utc", return_value=trusted_now
        ) as clock, mock.patch.object(
            contract_proposal,
            "build_external_contract_proposal",
            return_value={"sentinel": True},
        ) as build, mock.patch("sys.stdout", new=io.StringIO()):
            result = contract_proposal.main([
                "snapshot.json",
                "qualification.json",
                "brief.json",
                "--json",
            ])

        self.assertEqual(result, 0)
        self.assertEqual(load_json.call_count, 3)
        clock.assert_called_once_with()
        build.assert_called_once_with({}, {}, {}, as_of=trusted_now)

    def test_internal_clock_is_canonical_second_precision_utc(self):
        value = contract_proposal._trusted_current_utc()
        self.assertTrue(value.endswith("Z"))
        self.assertNotIn(".", value)
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(parsed.strftime("%Y-%m-%dT%H:%M:%SZ"), value)


if __name__ == "__main__":
    unittest.main()
