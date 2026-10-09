# SPDX-License-Identifier: MIT
"""Five focused inert fixtures: no untrusted code is executed."""
import tempfile
import unittest
from pathlib import Path
from concierge.repo_exec_autoexec import scan_autoexec, _git_blob_sha


class AutoExecSourceChecks(unittest.TestCase):
    def test_preinstall_requires_review_even_when_target_is_in_skipped_dist(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text('{"scripts":{"preinstall":"node dist/setup.js"}}')
            (root / "dist").mkdir()
            (root / "dist/setup.js").write_text('console.log("not executed")')
            r = scan_autoexec(root)
            self.assertEqual(len(r), 1)
            self.assertEqual(r[0]["status"], "REVIEW_REQUIRED")
            self.assertEqual(r[0]["hooks"], ["preinstall"])

    def test_no_lifecycle_hook_is_not_flagged_as_executing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text('{"scripts":{"test":"echo noop"}}')
            self.assertEqual(scan_autoexec(root)[0]["status"], "NO_KNOWN_INDICATOR")

    def test_vscode_folder_open_jsonc_requires_review(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".vscode").mkdir()
            (root / ".vscode/tasks.json").write_text('{"tasks":[{"runOptions":{"runOn":"folderOpen"}}], //comment\n}')
            r = scan_autoexec(root)
            self.assertEqual(r[0]["status"], "REVIEW_REQUIRED")
            self.assertEqual(r[0]["path"], ".vscode/tasks.json")

    def test_disguised_font_is_review_and_real_magic_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fonts = root / "public/fonts"
            fonts.mkdir(parents=True)
            (fonts / "fa-solid-500.woff2").write_text("not a WOFF2 font")
            (fonts / "valid.woff2").write_bytes(b"wOF2" + b"\x00\x01")
            results = {r["path"]: r["status"] for r in scan_autoexec(root)}
            self.assertEqual(results["public/fonts/fa-solid-500.woff2"], "REVIEW_REQUIRED")
            self.assertEqual(results["public/fonts/valid.woff2"], "NO_KNOWN_INDICATOR")

    def test_known_bad_blob_in_font_is_quarantined(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fonts = root / "public/fonts"
            fonts.mkdir(parents=True)
            (fonts / "bad.woff2").write_bytes(b"bad inert source fixture")
            self.assertEqual(scan_autoexec(root, hostile_blobs={_git_blob_sha(b"bad inert source fixture")})[0]["status"], "KNOWN_HOSTILE_BLOB")


if __name__ == "__main__":
    unittest.main()
