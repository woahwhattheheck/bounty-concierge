from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.bounty_value_router import (
    BountyValueRoutingInputError,
    compile_bounty_value_routing,
    verify_receipt,
)

POLICY = {
    "schema": "bounty-value-routing-policy/v1",
    "max_evidence_age_seconds": 86400,
    "routes": {"main_queue": "bug-bounty", "pile_10_49": "bounty-pile-10-49"},
    "assets": {
        "USD": {"active_floor": "50", "pile_floor": "10"},
        "USDC": {"active_floor": "50", "pile_floor": "10"},
    },
}

def evidence(amount="50", *, asset="USD", scope="ISSUE_SPECIFIC",
             authority="FIRST_PARTY", observed_at="2026-09-19T22:00:00Z",
             url="https://github.com/acme/widget/issues/1"):
    return {
        "scope": scope, "authority": authority, "amount": amount, "asset": asset,
        "evidence_url": url, "observed_at": observed_at,
    }

def candidate(work_id="w1", evidence_items=None, *, source=None):
    return {
        "work_id": work_id,
        "canonical_source_url": source or f"https://github.com/acme/widget/issues/{work_id[1:] or '1'}",
        "reward_evidence": list(evidence_items if evidence_items is not None else [evidence()]),
    }

def request(*items, policy=None, evaluated_at="2026-09-19T23:00:00Z"):
    return {
        "schema":"bounty-value-routing/v1",
        "policy": deepcopy(policy or POLICY),
        "evaluated_at": evaluated_at,
        "candidates": list(items),
    }

class BountyValueRouterTests(unittest.TestCase):
    def test_exact_50_routes_main(self):
        receipt = compile_bounty_value_routing(request(candidate()))
        row = receipt["candidates"][0]
        self.assertEqual(row["disposition"], "VALUE_50_PLUS")
        self.assertEqual(row["recommended_route"], "bug-bounty")

    def test_exact_50_usdc_routes_main_without_fx(self):
        row = compile_bounty_value_routing(
            request(candidate(evidence_items=[evidence("50", asset="USDC")]))
        )["candidates"][0]
        self.assertEqual(row["disposition"], "VALUE_50_PLUS")
        self.assertEqual(row["selected_amount"]["asset"], "USDC")
        self.assertFalse(compile_bounty_value_routing(request(candidate()))["authority"]["fx_conversion"])

    def test_10_through_under_50_routes_pile(self):
        for amount in ("10", "20", "49.999"):
            with self.subTest(amount=amount):
                row = compile_bounty_value_routing(
                    request(candidate(evidence_items=[evidence(amount)]))
                )["candidates"][0]
                self.assertEqual(row["disposition"], "PILE_10_49")
                self.assertEqual(row["recommended_route"], "bounty-pile-10-49")

    def test_under_10_drops(self):
        row = compile_bounty_value_routing(
            request(candidate(evidence_items=[evidence("9.99")]))
        )["candidates"][0]
        self.assertEqual(row["disposition"], "DROP_UNDER_10")
        self.assertIsNone(row["recommended_route"])

    def test_pile_does_not_auto_promote_as_batch(self):
        receipt = compile_bounty_value_routing(request(
            candidate(
                "w1",
                [evidence("20", url="https://github.com/acme/a/issues/1")],
                source="https://github.com/acme/a/issues/1",
            ),
            candidate(
                "w2",
                [evidence("20", url="https://github.com/acme/a/issues/2")],
                source="https://github.com/acme/a/issues/2",
            ),
            candidate(
                "w3",
                [evidence("20", url="https://github.com/acme/a/issues/3")],
                source="https://github.com/acme/a/issues/3",
            ),
        ))
        self.assertEqual(receipt["counts"]["PILE_10_49"], 3)
        self.assertEqual(receipt["counts"]["VALUE_50_PLUS"], 0)
        self.assertFalse(receipt["authority"]["automatic_batch_promotion"])

    def test_generic_program_amount_cannot_promote_issue(self):
        row = compile_bounty_value_routing(request(
            candidate(evidence_items=[
                evidence("100", scope="PROGRAM_GENERIC", url="https://example.com/bounty-policy")
            ])
        ))["candidates"][0]
        self.assertEqual(row["disposition"], "HOLD_ISSUE_AMOUNT_UNVERIFIED")

    def test_issue_specific_classification_overrides_generic_program_tier(self):
        row = compile_bounty_value_routing(request(
            candidate(evidence_items=[
                evidence("50", scope="PROGRAM_GENERIC", url="https://example.com/bounty-policy"),
                evidence(
                    "20",
                    authority="MAINTAINER",
                    url="https://github.com/acme/widget/issues/1#issuecomment-2",
                ),
            ])
        ))["candidates"][0]
        self.assertEqual(row["disposition"], "PILE_10_49")
        self.assertEqual(row["selected_amount"]["amount"], "20")

    def test_external_provider_amount_cannot_self_promote(self):
        row = compile_bounty_value_routing(request(
            candidate(evidence_items=[
                evidence(
                    "500",
                    authority="PROVIDER",
                    url="https://provider.example/bounties/acme-widget-1",
                )
            ])
        ))["candidates"][0]
        self.assertEqual(row["disposition"], "HOLD_ISSUE_AMOUNT_UNVERIFIED")
        self.assertIn("NO_FRESH_SOURCE_BOUND_ISSUE_AMOUNT", row["reason_codes"])

    def test_first_party_amount_for_different_issue_cannot_promote(self):
        row = compile_bounty_value_routing(request(
            candidate(evidence_items=[
                evidence("500", url="https://github.com/acme/widget/issues/2")
            ])
        ))["candidates"][0]
        self.assertEqual(row["disposition"], "HOLD_ISSUE_AMOUNT_UNVERIFIED")

    def test_milestone_specific_amount_does_not_promote_issue_in_v1(self):
        row = compile_bounty_value_routing(request(
            candidate(evidence_items=[
                evidence("500", scope="MILESTONE_SPECIFIC")
            ])
        ))["candidates"][0]
        self.assertEqual(row["disposition"], "HOLD_ISSUE_AMOUNT_UNVERIFIED")

    def test_maintainer_comment_on_exact_issue_is_source_bound(self):
        row = compile_bounty_value_routing(request(
            candidate(evidence_items=[
                evidence(
                    "75",
                    authority="MAINTAINER",
                    url="https://github.com/acme/widget/issues/1#issuecomment-123",
                )
            ])
        ))["candidates"][0]
        self.assertEqual(row["disposition"], "VALUE_50_PLUS")
        self.assertEqual(row["selected_amount"]["amount"], "75")

    def test_maintainer_comment_on_different_issue_cannot_promote(self):
        row = compile_bounty_value_routing(request(
            candidate(evidence_items=[
                evidence(
                    "500",
                    authority="MAINTAINER",
                    url="https://github.com/acme/widget/issues/2#issuecomment-123",
                )
            ])
        ))["candidates"][0]
        self.assertEqual(row["disposition"], "HOLD_ISSUE_AMOUNT_UNVERIFIED")

    def test_aggregator_only_amount_cannot_promote(self):
        row = compile_bounty_value_routing(request(
            candidate(evidence_items=[evidence("500", authority="AGGREGATOR")])
        ))["candidates"][0]
        self.assertEqual(row["disposition"], "HOLD_ISSUE_AMOUNT_UNVERIFIED")

    def test_conflicting_specific_amounts_hold(self):
        row = compile_bounty_value_routing(request(
            candidate(evidence_items=[
                evidence("100"),
                evidence(
                    "80",
                    authority="MAINTAINER",
                    url="https://github.com/acme/widget/issues/1#issuecomment-80",
                ),
            ])
        ))["candidates"][0]
        self.assertEqual(row["disposition"], "HOLD_CONFLICTING_SPECIFIC_AMOUNTS")

    def test_stale_specific_amount_holds(self):
        row = compile_bounty_value_routing(request(
            candidate(evidence_items=[evidence("500", observed_at="2026-09-17T00:00:00Z")])
        ))["candidates"][0]
        self.assertEqual(row["disposition"], "HOLD_STALE_SPECIFIC_AMOUNT")

    def test_unsupported_asset_holds_without_fx(self):
        row = compile_bounty_value_routing(request(
            candidate(evidence_items=[evidence("500", asset="EUR")])
        ))["candidates"][0]
        self.assertEqual(row["disposition"], "HOLD_UNSUPPORTED_ASSET")

    def test_bool_float_and_bad_policy_fail_closed(self):
        for bad in (True, 50.0, "NaN"):
            with self.subTest(value=bad):
                with self.assertRaises(BountyValueRoutingInputError):
                    compile_bounty_value_routing(
                        request(candidate(evidence_items=[evidence(bad)]))
                    )
        broken = deepcopy(POLICY)
        broken["assets"]["USD"]["pile_floor"] = "50"
        with self.assertRaises(BountyValueRoutingInputError):
            compile_bounty_value_routing(request(candidate(), policy=broken))

    def test_receipt_reconstructs_and_detects_tamper(self):
        receipt = compile_bounty_value_routing(request(candidate()))
        self.assertTrue(verify_receipt(receipt))
        changed = deepcopy(receipt)
        changed["candidates"][0]["disposition"] = "PILE_10_49"
        self.assertFalse(verify_receipt(changed))

    def test_checked_in_policy_matches_operator_floor(self):
        checked = json.loads(Path("policies/bounty_value_routing_v1.json").read_text())
        self.assertEqual(checked, POLICY)

    def test_cli_round_trip(self):
        payload = request(candidate())
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "request.json"
            p.write_text(json.dumps(payload))
            proc = subprocess.run(
                [sys.executable, "-m", "concierge.bounty_value_router", str(p), "--json"],
                text=True, capture_output=True, check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(verify_receipt(json.loads(proc.stdout)))

if __name__ == "__main__":
    unittest.main()
