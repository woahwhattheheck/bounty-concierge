from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import concierge.payoff_path_gate as gate


@unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "POSIX descriptor custody required")
class PayoffPathFileCustodyTests(unittest.TestCase):
    def test_read_pins_parent_generation_across_path_substitution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            source = live / "input.json"
            source.write_text("original", encoding="utf-8")
            outside = root / "outside"
            outside.mkdir()
            (outside / "input.json").write_text("foreign", encoding="utf-8")
            saved = root / "saved"

            original_open_parent = gate._open_parent

            def swapped(path: Path):
                parent_fd, name = original_open_parent(path)
                live.rename(saved)
                os.symlink(outside, live)
                return parent_fd, name

            with mock.patch.object(gate, "_open_parent", side_effect=swapped):
                self.assertEqual("original", gate._read_regular(source))
            self.assertEqual("foreign", source.read_text(encoding="utf-8"))

    def test_read_uses_open_descriptor_after_final_name_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.json"
            source.write_text("original", encoding="utf-8")
            moved = root / "moved.json"
            real_read = os.read
            fired = False

            def replacing_read(fd: int, size: int):
                nonlocal fired
                if not fired:
                    fired = True
                    source.rename(moved)
                    source.write_text("foreign", encoding="utf-8")
                return real_read(fd, size)

            with mock.patch.object(gate.os, "read", side_effect=replacing_read):
                with self.assertRaisesRegex(gate.PayoffPathError, "changed while being read"):
                    gate._read_regular(source)
            # The path now names the attacker/concurrent replacement, but those bytes
            # were never accepted as the validated input generation.
            self.assertEqual("foreign", source.read_text(encoding="utf-8"))

    def test_output_refuses_intermediate_symlink_without_foreign_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            alias = root / "alias"
            os.symlink(outside, alias)
            with self.assertRaises(gate.PayoffPathError):
                gate._publish_bundle([(alias / "packet.json", "payload")])
            self.assertFalse((outside / "packet.json").exists())

    def test_output_stays_with_pinned_parent_after_ancestor_swap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            outside = root / "outside"
            outside.mkdir()
            saved = root / "saved"
            destination = live / "packet.json"
            original_open_parent = gate._open_parent

            def swapped(path: Path):
                parent_fd, name = original_open_parent(path)
                live.rename(saved)
                os.symlink(outside, live)
                return parent_fd, name

            with mock.patch.object(gate, "_open_parent", side_effect=swapped):
                gate._publish_bundle([(destination, "payload")])
            self.assertEqual("payload", (saved / "packet.json").read_text(encoding="utf-8"))
            self.assertFalse((outside / "packet.json").exists())

    def test_post_create_failure_never_unlinks_foreign_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.txt"
            second = root / "second.txt"
            real_open_output = gate._open_output_fd
            calls = 0

            def fail_second(parent_fd: int, name: str):
                nonlocal calls
                calls += 1
                if calls == 2:
                    # Replace the already-created first dentry before failure.  A
                    # pathname rollback would delete this foreign generation.
                    first.unlink()
                    first.write_text("foreign-replacement", encoding="utf-8")
                    raise OSError("injected second-create failure")
                return real_open_output(parent_fd, name)

            with mock.patch.object(gate, "_open_output_fd", side_effect=fail_second):
                with self.assertRaises(gate.PayoffPathError):
                    gate._publish_bundle([(first, "ours"), (second, "two")])
            self.assertEqual("foreign-replacement", first.read_text(encoding="utf-8"))
            self.assertFalse(second.exists())

    def test_duplicate_alias_with_same_pinned_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(gate.PayoffPathError, "distinct"):
                gate._publish_bundle([(root / "x", "one"), (root / "x", "two")])


if __name__ == "__main__":
    unittest.main()
