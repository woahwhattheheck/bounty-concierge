# SPDX-License-Identifier: MIT
"""Fail-closed regressions for README bounty-index rendering."""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from concierge import readme_sync


class ReadmeSyncPayloadFailClosedTests(unittest.TestCase):
    def _build_from(self, payload: object) -> str:
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = pathlib.Path(tmpdir) / "bounty_index.json"
            index_path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.object(readme_sync, "INDEX_PATH", index_path):
                return readme_sync.build_section()

    def test_root_must_be_object(self) -> None:
        with self.assertRaisesRegex(ValueError, "root must be an object"):
            self._build_from([])

    def test_bounties_must_be_list(self) -> None:
        with self.assertRaisesRegex(ValueError, "'bounties' must be a list"):
            self._build_from({"bounties": {}})

    def test_rows_must_be_objects(self) -> None:
        with self.assertRaisesRegex(ValueError, r"bounties\[0\] must be an object"):
            self._build_from({"bounties": [None]})

    def test_renderer_fields_fail_closed_before_sort_or_join(self) -> None:
        bad_fields = (
            ({"reward_rtc": "10"}, "reward_rtc"),
            ({"number": "7"}, "number"),
            ({"repo": 7}, "repo"),
            ({"title": ["title"]}, "title"),
            ({"url": 7}, "url"),
            ({"difficulty": {}}, "difficulty"),
            ({"skills": "python"}, "skills"),
            ({"skills": ["python", None]}, "skills"),
        )
        for row, field in bad_fields:
            with self.subTest(field=field, row=row):
                with self.assertRaisesRegex(ValueError, field):
                    readme_sync.render_table([row])

    def test_nonfinite_and_bool_numeric_fields_are_rejected(self) -> None:
        for reward in (float("nan"), float("inf"), float("-inf"), True):
            with self.subTest(reward=reward):
                with self.assertRaisesRegex(ValueError, "reward_rtc"):
                    readme_sync.render_table([{"reward_rtc": reward}])
        with self.assertRaisesRegex(ValueError, "number"):
            readme_sync.render_table([{"number": False}])

    def test_main_returns_one_for_valid_json_with_invalid_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = pathlib.Path(tmpdir) / "bounty_index.json"
            index_path.write_text("[]", encoding="utf-8")
            with mock.patch.object(readme_sync, "INDEX_PATH", index_path):
                self.assertEqual(readme_sync.main([]), 1)

    def test_current_row_shape_still_renders(self) -> None:
        section = self._build_from(
            {
                "updated_at": "2026-09-12T10:33:46+00:00",
                "bounties": [
                    {
                        "repo": "Scottcjn/rustchain-bounties",
                        "number": 2451,
                        "title": "Founding miners",
                        "url": "https://github.com/Scottcjn/rustchain-bounties/issues/2451",
                        "reward_rtc": 3333.0,
                        "difficulty": "critical",
                        "skills": ["docker", "documentation", "rust"],
                    }
                ],
            }
        )
        self.assertIn("top 1 open bounties", section)
        self.assertIn("| rustchain-bounties | [#2451]", section)
        self.assertIn("| 3333 | critical | docker, documentation, rust |", section)


if __name__ == "__main__":
    unittest.main()
