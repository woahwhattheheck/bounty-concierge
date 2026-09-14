from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge import _revenue_settlement_core as core


ROW = {
    "type": "transfer_in",
    "from": "sponsor",
    "amount": "5",
    "tx_hash": "a" * 64,
    "timestamp": "2026-09-14T00:00:00Z",
}


class FlippingGetDict(dict):
    """Expose a fake first semantic view if a caller re-reads via get()."""

    def __init__(self):
        super().__init__(
            ok=True,
            miner_id="alice",
            transactions=[ROW],
            total=2,
        )
        self.get_calls = 0

    def get(self, key, default=None):
        self.get_calls += 1
        if key == "total" and self.get_calls <= 4:
            return 1
        return super().get(key, default)


class CompleteThenPartialGetDict(dict):
    """Return a changed total on later get() calls if semantics re-read it."""

    def __init__(self):
        super().__init__(
            ok=True,
            miner_id="alice",
            transactions=[ROW],
            total=1,
        )
        self.get_calls = 0

    def get(self, key, default=None):
        self.get_calls += 1
        if key == "total" and self.get_calls > 1:
            return 2
        return super().get(key, default)


class CoreHistoryCompletenessTests(unittest.TestCase):
    def test_direct_core_rejects_incomplete_canonical_snapshot(self):
        with self.assertRaisesRegex(
            core.RevenueSettlementInputError,
            "metadata was invalid",
        ):
            core._history_capture(
                {
                    "ok": True,
                    "miner_id": "alice",
                    "transactions": [ROW],
                    "total": 2,
                },
                wallet="alice",
            )

    def test_direct_core_accepts_complete_canonical_snapshot(self):
        rows, wallet = core._history_capture(
            {
                "ok": True,
                "miner_id": "alice",
                "transactions": [ROW],
                "total": 1,
            },
            wallet="alice",
        )
        self.assertEqual(rows, [ROW])
        self.assertEqual(wallet, "alice")

    def test_bool_negative_and_oversize_totals_fail_closed(self):
        for total in (True, -1, core._MAX_ITEMS + 1):
            with self.subTest(total=total):
                with self.assertRaises(core.RevenueSettlementInputError):
                    core._history_capture(
                        {
                            "ok": True,
                            "miner_id": "alice",
                            "transactions": [],
                            "total": total,
                        },
                        wallet="alice",
                    )

    def test_normalized_operator_capture_remains_compatible(self):
        rows, wallet = core._history_capture(
            {
                "schema_version": 1,
                "source": "rustchain_wallet_history",
                "wallet": "alice",
                "items": [ROW],
            },
            wallet="alice",
        )
        self.assertEqual(rows, [ROW])
        self.assertEqual(wallet, "alice")

    def test_get_subclass_cannot_show_preflight_view_then_partial_core_view(self):
        payload = FlippingGetDict()
        with self.assertRaises(core.RevenueSettlementInputError):
            core._history_capture(payload, wallet="alice")
        self.assertEqual(payload.get_calls, 0)

    def test_complete_mapping_subclass_is_frozen_without_second_semantic_read(self):
        payload = CompleteThenPartialGetDict()
        rows, wallet = core._history_capture(payload, wallet="alice")
        self.assertEqual(rows, [ROW])
        self.assertEqual(wallet, "alice")
        self.assertEqual(payload.get_calls, 0)

    def test_historical_base_name_is_exact_core_identity(self):
        from concierge import _revenue_settlement_base as base

        self.assertIs(base, core)
        self.assertIs(base._history_capture, core._history_capture)

    def test_preserved_implementation_is_not_a_weaker_python_import_path(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("concierge._revenue_settlement_core_impl")

    def test_direct_core_cli_rejects_incomplete_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "closeout.json").write_text(
                json.dumps({"schema_version": 1, "items": []}),
                encoding="utf-8",
            )
            (root / "bindings.json").write_text(
                json.dumps({"schema_version": 1, "items": []}),
                encoding="utf-8",
            )
            (root / "history.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "miner_id": "alice",
                        "transactions": [ROW],
                        "total": 2,
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(Path(__file__).parents[1])
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "concierge._revenue_settlement_core",
                    str(root / "closeout.json"),
                    str(root / "bindings.json"),
                    "--wallet",
                    "alice",
                    "--history",
                    str(root / "history.json"),
                ],
                env=env,
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn(
                "canonical history capture wallet metadata was invalid",
                completed.stderr,
            )


if __name__ == "__main__":
    unittest.main()
