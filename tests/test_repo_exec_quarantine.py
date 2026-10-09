# SPDX-License-Identifier: MIT
"""Five focused, offline, non-executing cases for the ESLint incident guard."""
import tempfile
import unittest
from pathlib import Path

from concierge.repo_exec_quarantine import git_blob_sha, scan_repository


class IncidentGuardTests(unittest.TestCase):
    def test_fingerprint_is_git_blob_not_plain_sha(self):
        self.assertEqual(git_blob_sha(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")

    def test_known_bad_content_is_quarantined(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "eslint.config.js"
            p.write_bytes(b"hostile fixture bytes")
            result = scan_repository(Path(d), hostile_blobs={git_blob_sha(p.read_bytes())})
            self.assertEqual(result["disposition"], "QUARANTINE")
            self.assertEqual(result["checks"][0]["status"], "KNOWN_HOSTILE_BLOB")

    def test_nonmatching_config_is_not_claimed_safe(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "eslint.config.js").write_text("export default [];\n", encoding="utf-8")
            result = scan_repository(Path(d))
            self.assertEqual(result["disposition"], "NO_KNOWN_INDICATOR")
            self.assertIn("No clean/safe-to-execute", result["scope"])

    def test_obfuscated_eval_and_spawn_requires_review(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "eslint.config.js"
            p.write_text("const payload='" + "x" * 200 + "'; eval(payload); spawn('node', []);", encoding="utf-8")
            self.assertEqual(scan_repository(Path(d))["disposition"], "REVIEW_REQUIRED")

    def test_symlink_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "outside.txt").write_text("not a config")
            (root / "eslint.config.js").symlink_to(root / "outside.txt")
            result = scan_repository(root)
            self.assertEqual(result["disposition"], "REVIEW_REQUIRED")
            self.assertEqual(result["checks"][0]["reason"], "symlink not followed")

    def test_confirmed_variant_fingerprint_blocked(self):
        from concierge.repo_exec_quarantine import HOSTILE_GIT_BLOBS

        self.assertIn("7da565bcb57517fa1c3adc1c824b7e105dae2699", HOSTILE_GIT_BLOBS)
        self.assertIn("0290e72b7db38a21c14e86357e2002d7f00709e3", HOSTILE_GIT_BLOBS)


if __name__ == "__main__":
    unittest.main()
