# SPDX-License-Identifier: MIT
"""Six inert, offline checks for nested ESLint and PostCSS loader variants."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from concierge.repo_exec_quarantine import scan_repository, git_blob_sha

# Structural fixture only: never executed as source code.
FAMILY = (
    "function GSkqNNyuJw$_padNcYwam(){};\n"
    "const NONCE_FANOUT = 1;\n"
    "eval(encoded); spawn('node', args);\n"
)


class NestedConfigQuarantineTest(unittest.TestCase):
    def test_nested_postcss_family_quarantined(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "apps" / "web" / "postcss.config.mjs"
            path.parent.mkdir(parents=True)
            path.write_text(FAMILY, encoding="utf-8")
            r = scan_repository(Path(d))
            self.assertEqual(r["disposition"], "QUARANTINE")
            self.assertEqual(r["checks"][0]["path"], "apps/web/postcss.config.mjs")
            self.assertEqual(r["checks"][0]["status"], "KNOWN_HOSTILE_FAMILY")

    def test_nested_eslint_family_quarantined(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "packages" / "ui" / "eslint.config.mjs"
            path.parent.mkdir(parents=True)
            path.write_text(FAMILY, encoding="utf-8")
            self.assertEqual(scan_repository(Path(d))["disposition"], "QUARANTINE")

    def test_postcss_exact_blob_matches_even_without_family_marker(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "postcss.config.mjs"
            path.write_bytes(b"fixture pinned git blob")
            r = scan_repository(Path(d), hostile_blobs={git_blob_sha(path.read_bytes())})
            self.assertEqual(r["checks"][0]["status"], "KNOWN_HOSTILE_BLOB")

    def test_nested_benign_and_incident_markdown_not_automatically_bad(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / "apps" / "web"
            p.mkdir(parents=True)
            (p / "postcss.config.mjs").write_text("export default {plugins:{}};\n")
            (root / "INCIDENT.md").write_text(FAMILY)
            r = scan_repository(root)
            self.assertEqual(r["disposition"], "NO_KNOWN_INDICATOR")
            self.assertIn("No clean/safe-to-execute", r["scope"])

    def test_symlinked_config_directory_is_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "repo"
            root.mkdir()
            hidden = Path(d) / "outside"
            hidden.mkdir()
            (hidden / "postcss.config.mjs").write_text(FAMILY)
            (root / "apps").symlink_to(hidden, target_is_directory=True)
            r = scan_repository(root)
            self.assertEqual(r["disposition"], "REVIEW_REQUIRED")
            self.assertTrue(any(c["path"] == "apps" and c["reason"] == "symlink directory not followed"
                                for c in r["checks"]))

    def test_config_count_overflow_is_explicit_review_not_clean(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "eslint.config.js").write_text("export default {};\n")
            (root / "postcss.config.mjs").write_text("export default {};\n")
            with patch("concierge.repo_exec_quarantine.MAX_SCAN_FILES", 1):
                r = scan_repository(root)
            self.assertEqual(r["disposition"], "REVIEW_REQUIRED")
            self.assertTrue(any(c.get("reason", "").startswith("configuration scan limit")
                                for c in r["checks"]))


if __name__ == "__main__":
    unittest.main()
