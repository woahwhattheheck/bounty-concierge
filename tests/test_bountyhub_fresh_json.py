"""Focused regressions for retained-input parsing; no provider requests."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from concierge.bountyhub_fresh_targets import _load


class FreshTargetJSONTests(unittest.TestCase):
    def load_text(self, text):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "retained.json"
            path.write_text(text, encoding="utf-8")
            return _load(path)

    def test_valid_and_absent_inputs_are_unchanged(self):
        self.assertIsNone(_load(None))
        self.assertEqual(self.load_text('{"records":[{"reason":"closed"}]}'),
                         {"records": [{"reason": "closed"}]})

    def test_duplicate_issue_records_are_not_silently_replaced(self):
        with self.assertRaises(ValueError):
            self.load_text('{"owner/repo#1":{"reason":"closed"},'
                           '"owner/repo#1":{"reason":"submitted"}}')

    def test_duplicate_nested_evidence_fields_are_not_silently_replaced(self):
        with self.assertRaises(ValueError):
            self.load_text('{"owner/repo#1":{"reason":"closed",'
                           '"reason":"submitted"}}')


if __name__ == "__main__":
    unittest.main()
