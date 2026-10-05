"""Focused checks for exact PR changed-file scope comparison."""

import importlib.util
from pathlib import Path
import sys
import unittest

SPEC = importlib.util.spec_from_file_location(
    "pr_scope_guard", Path(__file__).parents[1] / "tools/pr_scope_guard.py"
)
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


class PrScopeGuardTest(unittest.TestCase):
    def test_exact_scope_is_order_independent(self):
        result = guard.compare_scope(
            ["backend/src/service.ts", "backend/src/service.test.ts"],
            ["backend/src/service.test.ts", "backend/src/service.ts"],
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.unexpected, [])
        self.assertEqual(result.missing, [])

    def test_scope_mismatch_reports_unexpected_and_missing_paths(self):
        result = guard.compare_scope(
            ["src/expected.py", "tests/test_expected.py"],
            ["src/expected.py", ".github/workflows/build.yml"],
        )

        self.assertEqual(result.status, "scope_mismatch")
        self.assertEqual(result.unexpected, [".github/workflows/build.yml"])
        self.assertEqual(result.missing, ["tests/test_expected.py"])


if __name__ == "__main__":
    unittest.main()
