import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.bounty_value_router import compile_bounty_value_routing
from concierge.bounty_supply_gate import BountySupplyGateInputError, _fixed_floor, compile_bounty_supply_gate, verify_receipt

POLICY = {
    "schema": "bounty-value-routing-policy/v1", "max_evidence_age_seconds": 86400,
    "routes": {"main_queue": "bug-bounty", "pile_10_49": "bounty-pile-10-49"},
    "assets": {"USD": {"active_floor": "50", "pile_floor": "10"}, "USDC": {"active_floor": "50", "pile_floor": "10"}},
}


def evidence(amount="75", authority="FIRST_PARTY"):
    return {"scope": "ISSUE_SPECIFIC", "authority": authority, "amount": amount, "asset": "USD",
            "evidence_url": "https://github.com/acme/widget/issues/1", "observed_at": "2026-09-19T23:40:00Z"}


def candidate(work_id="w1", amount="75", evidence_items=None):
    return {"work_id": work_id, "canonical_source_url": f"https://github.com/acme/widget/issues/{work_id[1:] or '1'}",
            "reward_evidence": list(evidence_items if evidence_items is not None else [evidence(amount)])}


def value_receipt(*candidates, policy=POLICY):
    return compile_bounty_value_routing({"schema": "bounty-value-routing/v1", "policy": policy,
        "evaluated_at": "2026-09-19T23:50:00Z", "candidates": list(candidates)})


def snap(work_id="w1", **overrides):
    row = {"work_id": work_id, "canonical_source_url": f"https://github.com/acme/widget/issues/{work_id[1:] or '1'}",
        "observed_at": "2026-09-19T23:45:00Z", "primary_state": "OPEN", "primary_state_reason": None,
        "maintainer_hold": False, "exclusive_assignment": False, "assignees": [], "source_census_complete": True,
        "carrier_census_complete": True, "active_carrier_count": 0, "carrier_urls": [], "marketplace_advertised_open": True}
    row.update(overrides)
    return row


def req(candidates=None, snaps=None, value=None, evaluated_at="2026-09-19T23:50:00Z"):
    candidates = [candidate()] if candidates is None else candidates
    snaps = [snap()] if snaps is None else snaps
    return {"schema": "bounty-supply-normalizer/v1", "evaluated_at": evaluated_at,
            "value_receipt": value or value_receipt(*candidates), "availability": snaps}


class GateTests(unittest.TestCase):
    def disp(self, request):
        return compile_bounty_supply_gate(request)["candidates"][0]["disposition"]

    def test_clean_75_normalizes_and_receipt_verifies(self):
        out = compile_bounty_supply_gate(req())
        self.assertEqual(out["counts"]["SNAPSHOT_NORMALIZED_FOR_VIABILITY"], 1)
        self.assertEqual(out["candidates"][0]["recommended_route"], "bounty-canonical-viability")
        self.assertTrue(verify_receipt(out))

    def test_primary_closed_beats_stale_marketplace_open(self):
        row = compile_bounty_supply_gate(req(snaps=[snap(primary_state="CLOSED", primary_state_reason="NOT_PLANNED")]))["candidates"][0]
        self.assertEqual(row["disposition"], "SUPPRESS_PRIMARY_CLOSED")
        self.assertIn("MARKETPLACE_OPEN_CONFLICTS_WITH_PRIMARY", row["reason_codes"])

    def test_terminal_suppressions(self):
        cases = [
            (snap(maintainer_hold=True), "SUPPRESS_MAINTAINER_HOLD"),
            (snap(exclusive_assignment=True), "SUPPRESS_EXCLUSIVE_ASSIGNMENT"),
            (snap(active_carrier_count=1), "SUPPRESS_EXISTING_CARRIER"),
            (snap(carrier_census_complete=False, active_carrier_count=1), "SUPPRESS_EXISTING_CARRIER"),
        ]
        for snapshot, expected in cases:
            with self.subTest(expected=expected): self.assertEqual(self.disp(req(snaps=[snapshot])), expected)

    def test_incomplete_or_unknown_evidence_holds(self):
        cases = [
            (snap(source_census_complete=False), "HOLD_SOURCE_CENSUS_INCOMPLETE"),
            (snap(carrier_census_complete=False), "HOLD_CARRIER_CENSUS_INCOMPLETE"),
            (snap(primary_state="UNKNOWN"), "HOLD_PRIMARY_STATE_UNKNOWN"),
            (snap(observed_at="2026-09-19T23:00:00Z"), "HOLD_AVAILABILITY_STALE"),
        ]
        for snapshot, expected in cases:
            with self.subTest(expected=expected): self.assertEqual(self.disp(req(snaps=[snapshot])), expected)

    def test_low_and_aggregator_only_value_hold(self):
        cases = [candidate(amount="20"), candidate(amount="5"), candidate(evidence_items=[evidence("500", "AGGREGATOR")])]
        for c in cases:
            with self.subTest(c=c): self.assertEqual(self.disp(req(candidates=[c])), "HOLD_VALUE_NOT_ACTIVE")

    def test_fixed_50_floor_is_source_owned_independent_of_child_policy(self):
        self.assertFalse(_fixed_floor({
            "disposition": "VALUE_50_PLUS",
            "selected_amount": {"amount": "5", "asset": "USD"},
        }))
        self.assertTrue(_fixed_floor({
            "disposition": "VALUE_50_PLUS",
            "selected_amount": {"amount": "50", "asset": "USD"},
        }))
        self.assertFalse(_fixed_floor({
            "disposition": "VALUE_50_PLUS",
            "selected_amount": {"amount": "500", "asset": "EUR"},
        }))

    def test_marketplace_nonopen_holds_for_downstream_recheck(self):
        for advertised in (False, None):
            with self.subTest(advertised=advertised):
                self.assertEqual(self.disp(req(snaps=[snap(marketplace_advertised_open=advertised)])), "HOLD_MARKETPLACE_STATE_NOT_OPEN")

    def test_identity_and_cardinality_fail_closed(self):
        with self.assertRaisesRegex(BountySupplyGateInputError, "missing availability"):
            compile_bounty_supply_gate(req(candidates=[candidate("w1")], snaps=[snap("w2")]))
        with self.assertRaisesRegex(BountySupplyGateInputError, "candidates absent"):
            compile_bounty_supply_gate(req(candidates=[candidate("w1")], snaps=[snap("w1"), snap("w2")]))
        empty = value_receipt()
        with self.assertRaisesRegex(BountySupplyGateInputError, "non-empty"):
            compile_bounty_supply_gate(req(candidates=[], snaps=[], value=empty))

    def test_availability_order_is_canonical(self):
        candidates = [candidate("w2"), candidate("w1")]; snaps = [snap("w1", maintainer_hold=True), snap("w2")]
        self.assertEqual(compile_bounty_supply_gate(req(candidates, snaps)), compile_bounty_supply_gate(req(candidates, list(reversed(snaps)))))

    def test_value_time_and_semantic_tamper_fail(self):
        bad = req(); bad["evaluated_at"] = "2026-09-19T23:51:00Z"
        with self.assertRaisesRegex(BountySupplyGateInputError, "exactly match"): compile_bounty_supply_gate(bad)
        bad = req(); bad["value_receipt"]["candidates"][0]["disposition"] = "PILE_10_49"
        with self.assertRaisesRegex(BountySupplyGateInputError, "semantic verification"): compile_bounty_supply_gate(bad)

    def test_subsecond_freshness_does_not_floor_into_window(self):
        request = req(
            snaps=[snap(observed_at="2026-09-19T23:35:00Z")],
            evaluated_at="2026-09-19T23:50:00.999000Z",
        )
        request["value_receipt"] = value_receipt(candidate())
        request["value_receipt"]["input"]["evaluated_at"] = request["evaluated_at"]
        # Recompiling is required because semantic receipt verification binds evaluated_at.
        request["value_receipt"] = compile_bounty_value_routing({
            "schema": "bounty-value-routing/v1", "policy": POLICY,
            "evaluated_at": request["evaluated_at"], "candidates": [candidate()]
        })
        self.assertEqual(self.disp(request), "HOLD_AVAILABILITY_STALE")

    def test_nested_caller_owned_json_subclass_is_rejected(self):
        class ReflectiveDict(dict):
            def __eq__(self, other):
                return True
        request = req()
        request["value_receipt"]["authority"] = ReflectiveDict(request["value_receipt"]["authority"])
        with self.assertRaisesRegex(BountySupplyGateInputError, "exact built-in JSON types"):
            compile_bounty_supply_gate(request)
        receipt = compile_bounty_supply_gate(req())
        receipt["authority"] = ReflectiveDict(receipt["authority"])
        self.assertFalse(verify_receipt(receipt))

    def test_trust_model_demotes_caller_snapshot(self):
        out = compile_bounty_supply_gate(req())
        self.assertEqual(out["trust_model"]["availability"], "CALLER_ASSERTED_UNVERIFIED_ORCHESTRATOR_SNAPSHOT")
        self.assertTrue(out["trust_model"]["downstream_canonical_reverification_required"])
        self.assertFalse(out["authority"]["source_or_collision_evidence_authority"])

    def test_output_tamper_detected(self):
        out = compile_bounty_supply_gate(req()); out["candidates"][0]["disposition"] = "SUPPRESS_PRIMARY_CLOSED"
        self.assertFalse(verify_receipt(out))

    def test_cli_strict_json_and_nonactive_exit(self):
        with tempfile.TemporaryDirectory() as d:
            dup = Path(d) / "dup.json"; dup.write_text('{"schema":"bounty-supply-normalizer/v1","schema":"x"}')
            p = subprocess.run([sys.executable, "-m", "concierge.bounty_supply_gate", str(dup)], text=True, capture_output=True)
            self.assertNotEqual(p.returncode, 0); self.assertIn("duplicate JSON key", p.stderr)
            nan = Path(d) / "nan.json"; nan.write_text('{"schema":NaN}')
            p = subprocess.run([sys.executable, "-m", "concierge.bounty_supply_gate", str(nan)], text=True, capture_output=True)
            self.assertNotEqual(p.returncode, 0); self.assertIn("non-finite JSON constant", p.stderr)
            hold = Path(d) / "hold.json"; hold.write_text(json.dumps(req(snaps=[snap(maintainer_hold=True)])))
            p = subprocess.run([sys.executable, "-m", "concierge.bounty_supply_gate", str(hold), "--json"], text=True, capture_output=True)
            self.assertEqual(p.returncode, 2, p.stderr)


if __name__ == "__main__": unittest.main()
