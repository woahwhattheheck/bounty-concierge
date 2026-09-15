from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from concierge import payout_delivery_gate as payout
from concierge import secure_output


class SecureOutputTests(unittest.TestCase):
    def require_supported_host(self) -> None:
        if (
            not getattr(os, "O_NOFOLLOW", 0)
            or not getattr(os, "O_DIRECTORY", 0)
            or os.open not in getattr(os, "supports_dir_fd", set())
        ):
            self.skipTest("host lacks no-follow dirfd traversal")

    def test_normal_create_is_exclusive_regular_and_private(self):
        self.require_supported_host()
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "nested"
            parent.mkdir()
            target = parent / "receipt.json"
            payout._write_new(target, b"receipt\n")
            self.assertEqual(target.read_bytes(), b"receipt\n")
            self.assertTrue(stat.S_ISREG(target.stat().st_mode))
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            with self.assertRaises(payout.PayoutDeliveryInputError):
                payout._write_new(target, b"replacement\n")

    def test_symlinked_parent_is_rejected_without_creating_leaf(self):
        self.require_supported_host()
        if not hasattr(os, "symlink"):
            self.skipTest("host lacks symlink support")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(
                payout.PayoutDeliveryInputError,
                "symlinked or non-directory output parent",
            ):
                payout._write_new(link / "receipt.json", b"receipt\n")
            self.assertFalse((real / "receipt.json").exists())

    def test_nested_symlinked_parent_is_rejected(self):
        self.require_supported_host()
        if not hasattr(os, "symlink"):
            self.skipTest("host lacks symlink support")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            safe = root / "safe"
            safe.mkdir()
            real = root / "real"
            real.mkdir()
            (safe / "redirect").symlink_to(real, target_is_directory=True)
            with self.assertRaises(payout.PayoutDeliveryInputError):
                payout._write_new(safe / "redirect" / "receipt.json", b"receipt\n")
            self.assertFalse((real / "receipt.json").exists())

    def test_relative_parent_escape_is_rejected(self):
        self.require_supported_host()
        with tempfile.TemporaryDirectory() as tmp:
            previous = Path.cwd()
            os.chdir(tmp)
            try:
                with self.assertRaisesRegex(
                    payout.PayoutDeliveryInputError, "parent traversal with '\\.\\.'"
                ):
                    payout._write_new(Path("..") / "receipt.json", b"receipt\n")
            finally:
                os.chdir(previous)

    def test_failed_write_unlinks_through_verified_parent(self):
        self.require_supported_host()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "receipt.json"
            with mock.patch.object(secure_output.os, "write", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    payout._write_new(target, b"receipt\n")
            self.assertFalse(target.exists())

    def test_unsupported_host_fails_closed(self):
        with mock.patch.object(secure_output.os, "supports_dir_fd", set()):
            with self.assertRaisesRegex(
                payout.PayoutDeliveryInputError, "requires O_NOFOLLOW/O_DIRECTORY"
            ):
                payout._write_new(Path("receipt.json"), b"receipt\n")


if __name__ == "__main__":
    unittest.main()
