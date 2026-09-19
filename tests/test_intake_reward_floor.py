from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.intake_reward_floor import (
    RewardFloorInputError,
    compile_intake_reward_floor,
    verify_receipt,
)


POLICY = {
    "schema": "intake-reward-floor-policy/v1",
    "active_floor_usd": "50",
    "pile_floor_usd": "10",
}


def candidate(work_id="x", *, state="KNOWN_USD", reward="50", source=None):
    item = {
        "work_id": work_id,
        "canonical_source_url": source or f"https://github.com/example/repo/issues/{work_id}",
        "reward_state": state,
        "reward_evidence_url": source or f"https://github.com/example/repo/issues/{work_id}",
        "observed_at": "2026-09-19T23:30:00Z",
    }
    if state == "KNOWN_USD":
        item["reward_usd"] = reward
    return item


def request(items):
    return {
        "schema": "intake-reward-floor/v1",
        "as_of": "2026-09-19T23:31:00Z",
        "policy": deepcopy(POLICY),
        "candidates": items,
    }


class RewardFloorTests(unittest.TestCase):
    def route(self, amount):
        receipt = compile_intake_reward_floor(request([candidate(reward=amount)]))
        self.assertTrue(verify_receipt(receipt))
        return receipt["candidates"][0]

    def test_exact_50_is_active(self):
        item = self.route("50")
        self.assertEqual(item["disposition"], "ACTIVE_FLOOR_MET")
        self.assertTrue(item["active_queue_eligible"])

    def test_above_50_is_active(self):
        self.assertEqual(self.route("250")["disposition"], "ACTIVE_FLOOR_MET")

    def test_exact_10_goes_to_pile(self):
        self.assertEqual(self.route("10")["disposition"], "PILE_10_49")

    def test_49_99_goes_to_pile(self):
        self.assertEqual(self.route("49.99")["disposition"], "PILE_10_49")

    def test_under_10_is_ignored(self):
        self.assertEqual(self.route("9.99")["disposition"], "IGNORE_UNDER_10")

    def test_zero_is_ignored(self):
        self.assertEqual(self.route("0")["disposition"], "IGNORE_UNDER_10")

    def test_unknown_reward_is_hold_not_zero(self):
        item = candidate(state="UNKNOWN")
        item["native_reward_note"] = "GrantFox Maybe Rewarded; no exact amount exposed"
        receipt = compile_intake_reward_floor(request([item]))
        decision = receipt["candidates"][0]
        self.assertEqual(decision["disposition"], "HOLD_UNCONFIRMED")
        self.assertIsNone(decision["reward_usd"])
        self.assertIn("EXACT_USD_REWARD_NOT_VERIFIED", decision["reason_codes"])

    def test_unknown_must_not_assert_reward(self):
        item = candidate(state="UNKNOWN")
        item["reward_usd"] = "100"
        with self.assertRaisesRegex(RewardFloorInputError, "must not assert"):
            compile_intake_reward_floor(request([item]))

    def test_known_requires_reward(self):
        item = candidate()
        del item["reward_usd"]
        with self.assertRaisesRegex(RewardFloorInputError, "requires reward_usd"):
            compile_intake_reward_floor(request([item]))

    def test_no_batch_promotion(self):
        items = [candidate(str(i), reward="25") for i in range(30)]
        receipt = compile_intake_reward_floor(request(items))
        self.assertEqual(receipt["counts"]["PILE_10_49"], 30)
        self.assertEqual(receipt["counts"]["ACTIVE_FLOOR_MET"], 0)
        self.assertFalse(receipt["authority"]["batch_promotion"])

    def test_mixed_routing_counts(self):
        items = [
            candidate("a", reward="100"),
            candidate("b", reward="25"),
            candidate("c", reward="1"),
            candidate("d", state="UNKNOWN"),
        ]
        receipt = compile_intake_reward_floor(request(items))
        self.assertEqual(
            receipt["counts"],
            {
                "ACTIVE_FLOOR_MET": 1,
                "HOLD_UNCONFIRMED": 1,
                "IGNORE_UNDER_10": 1,
                "PILE_10_49": 1,
            },
        )

    def test_no_fx_claim(self):
        item = candidate(state="UNKNOWN")
        item["native_reward_note"] = "EUR 100 advertised; no exact USD authority supplied"
        receipt = compile_intake_reward_floor(request([item]))
        self.assertEqual(receipt["candidates"][0]["disposition"], "HOLD_UNCONFIRMED")
        self.assertFalse(receipt["authority"]["fx_conversion"])

    def test_float_money_is_rejected(self):
        item = candidate()
        item["reward_usd"] = 50.0
        with self.assertRaisesRegex(RewardFloorInputError, "exact decimal"):
            compile_intake_reward_floor(request([item]))

    def test_nan_and_negative_are_rejected(self):
        for bad in ("NaN", "Infinity", "-1"):
            with self.subTest(bad=bad):
                with self.assertRaises(RewardFloorInputError):
                    compile_intake_reward_floor(request([candidate(reward=bad)]))

    def test_duplicate_work_id_rejected(self):
        with self.assertRaisesRegex(RewardFloorInputError, "duplicate work_id"):
            compile_intake_reward_floor(
                request([candidate("a"), candidate("a", source="https://github.com/e/r/issues/2")])
            )

    def test_duplicate_source_rejected(self):
        source = "https://github.com/e/r/issues/2"
        with self.assertRaisesRegex(RewardFloorInputError, "duplicate canonical_source_url"):
            compile_intake_reward_floor(
                request([candidate("a", source=source), candidate("b", source=source)])
            )

    def test_non_https_and_fragment_rejected(self):
        for bad in (
            "http://github.com/e/r/issues/2",
            "https://github.com/e/r/issues/2#fragment",
        ):
            with self.subTest(bad=bad):
                item = candidate()
                item["canonical_source_url"] = bad
                with self.assertRaises(RewardFloorInputError):
                    compile_intake_reward_floor(request([item]))

    def test_policy_ordering_is_guarded(self):
        payload = request([candidate()])
        payload["policy"]["active_floor_usd"] = "10"
        payload["policy"]["pile_floor_usd"] = "10"
        with self.assertRaisesRegex(RewardFloorInputError, "must be below"):
            compile_intake_reward_floor(payload)

    def test_receipt_deterministic_and_tamper_evident(self):
        payload = request([candidate("a", reward="75"), candidate("b", state="UNKNOWN")])
        first = compile_intake_reward_floor(payload)
        second = compile_intake_reward_floor(deepcopy(payload))
        self.assertEqual(first, second)
        self.assertTrue(verify_receipt(first))
        changed = deepcopy(first)
        changed["candidates"][0]["disposition"] = "PILE_10_49"
        self.assertFalse(verify_receipt(changed))

    def test_active_boolean_must_match_disposition_even_if_rehashed(self):
        receipt = compile_intake_reward_floor(request([candidate()]))
        receipt["candidates"][0]["active_queue_eligible"] = False
        body = dict(receipt)
        body.pop("receipt_sha256")
        receipt["receipt_sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        self.assertFalse(verify_receipt(receipt))

    def test_checked_in_policy_matches_owner_floor(self):
        checked = json.loads(
            Path("policies/intake_reward_floor_v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(checked, POLICY)

    def test_checked_in_example_routes_all_four_queues(self):
        payload = json.loads(
            Path("examples/intake_reward_floor.example.json").read_text(encoding="utf-8")
        )
        receipt = compile_intake_reward_floor(payload)
        self.assertEqual(
            receipt["counts"],
            {
                "ACTIVE_FLOOR_MET": 1,
                "HOLD_UNCONFIRMED": 1,
                "IGNORE_UNDER_10": 1,
                "PILE_10_49": 1,
            },
        )
        self.assertTrue(verify_receipt(receipt))

    def test_cli_round_trip_and_summary(self):
        payload = request([
            candidate("a", reward="100"),
            candidate("b", reward="25"),
            candidate("c", reward="1"),
            candidate("d", state="UNKNOWN"),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "in.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "concierge.intake_reward_floor", str(path)],
                text=True, capture_output=True, check=False,
                cwd=str(Path(__file__).resolve().parents[1]),
            )
            json_proc = subprocess.run(
                [sys.executable, "-m", "concierge.intake_reward_floor", str(path), "--json"],
                text=True, capture_output=True, check=False,
                cwd=str(Path(__file__).resolve().parents[1]),
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("active=1 pile=1 hold=1 ignore=1", proc.stdout)
        self.assertEqual(json_proc.returncode, 0, json_proc.stderr)
        self.assertTrue(verify_receipt(json.loads(json_proc.stdout)))

    def test_duplicate_json_keys_fail_closed(self):
        raw = '{"schema":"intake-reward-floor/v1","schema":"other"}'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dupe.json"
            path.write_text(raw, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "concierge.intake_reward_floor", str(path)],
                text=True, capture_output=True, check=False,
                cwd=str(Path(__file__).resolve().parents[1]),
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("duplicate key", proc.stderr)


if __name__ == "__main__":
    unittest.main()
