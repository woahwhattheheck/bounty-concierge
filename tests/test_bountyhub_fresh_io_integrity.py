"""Focused offline BountyHub fresh-intake IO custody checks; no provider requests."""
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import redirect_stderr
from unittest import TestCase, mock

from concierge import bountyhub_fresh_targets as fresh


class FreshIOIntegrityTests(TestCase):
    def test_retained_json_cap_fails_before_parse(self):
        with TemporaryDirectory() as directory:
            report = Path(directory) / "oversized.json"
            report.write_bytes(b"{" + b"x" * 100)
            with mock.patch.object(fresh, "_MAX_INPUT_BYTES", 64):
                with self.assertRaisesRegex(ValueError, "exceeds"):
                    fresh._load(report)

    def _run_with_fake_selector(self, report, *args):
        fake = {"canonical_preflight_candidates": {"candidates": []}}
        with mock.patch.object(fresh, "select_fresh_targets", return_value=fake), \
             mock.patch("concierge.bountyhub_catalog._emit_json") as emit, \
             redirect_stderr(io.StringIO()):
            result = fresh.main([str(report), *map(str, args)])
        return result, emit

    def test_report_input_cannot_be_overwritten_by_output(self):
        with TemporaryDirectory() as directory:
            report = Path(directory) / "catalog.json"
            report.write_text("{}", encoding="utf-8")
            result, emit = self._run_with_fake_selector(report, "--output", report)
            self.assertEqual(result, 2)
            emit.assert_not_called()
            self.assertEqual(report.read_text(), "{}")

    def test_symlink_and_hardlink_aliases_are_rejected_before_any_write(self):
        with TemporaryDirectory() as directory:
            d = Path(directory)
            report = d / "catalog.json"
            report.write_text("{}", encoding="utf-8")
            symlink = d / "link.json"
            symlink.symlink_to(report)
            hardlink = d / "hardlink.json"
            os.link(report, hardlink)
            for candidate in (symlink, hardlink):
                result, emit = self._run_with_fake_selector(report, "--preflight-output", candidate)
                self.assertEqual(result, 2)
                emit.assert_not_called()
                self.assertEqual(report.read_text(), "{}")

    def test_two_output_aliases_cannot_replace_each_other(self):
        with TemporaryDirectory() as directory:
            d = Path(directory)
            report = d / "catalog.json"
            report.write_text("{}", encoding="utf-8")
            output = d / "targets.json"
            output.write_text("prior results", encoding="utf-8")
            alias = d / "output-link.json"
            alias.symlink_to(output)
            result, emit = self._run_with_fake_selector(
                report, "--output", output, "--preflight-output", alias
            )
            self.assertEqual(result, 2)
            emit.assert_not_called()
            self.assertEqual(output.read_text(), "prior results")


if __name__ == "__main__":
    import unittest
    unittest.main()
