from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge import payout_delivery_gate as m

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "concierge" / "payout_delivery_gate.py"


def h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def base():
    route = h("github:Scottcjn/Rustchain#100")
    request = {
        "schema": m.REQUEST_SCHEMA,
        "request_id": "rustchain-100-claim",
        "opportunity_ref": "Scottcjn/Rustchain#100",
        "target_kind": "GITHUB_COMMENT",
        "target_route_sha256": route,
        "work_url": "https://github.com/woahwhattheheck/bottube/pull/16",
        "work_sha": "c209eda74549ac424d113ad5abfbe6506ce59207",
        "reward_reference": "10 RTC advertised reward",
        "claimant_label": "woahwhattheheck",
        "source_authority_ref": "https://github.com/woahwhattheheck/bottube/pull/16",
        "source_authority_sha256": h("payout-packet"),
        "prepared_at": "2026-09-15T02:00:00Z",
        "message_sha256": h("claim message"),
    }
    digest = m.request_sha256(request)
    arbitration = {
        "schema": m.ARBITRATION_SCHEMA,
        "request_sha256": digest,
        "target_route_sha256": route,
        "decision": "SELECTED",
        "owner_seat": "ZMJ-V6R8",
        "arbiter": "Muse",
        "arbitration_ref": "https://tokenjunkielabs.slack.com/archives/C0C01AXLCGZ/p1789440700852759",
        "issued_at": "2026-09-15T02:01:00Z",
        "expires_at": "2026-09-15T02:11:00Z",
    }
    history = {
        "schema": m.HISTORY_SCHEMA,
        "request_sha256": digest,
        "target_route_sha256": route,
        "complete": True,
        "captured_at": "2026-09-15T02:02:00Z",
        "items": [],
        "terminal": None,
    }
    return request, arbitration, history


def mutation(
    request,
    arbitration,
    *,
    outcome="CONFIRMED_SENT",
    opportunity=None,
    request_digest=None,
    route=None,
    attempted="2026-09-15T02:01:30Z",
    mid="send-1",
):
    return {
        "mutation_id": mid,
        "request_sha256": request_digest or m.request_sha256(request),
        "target_route_sha256": route or request["target_route_sha256"],
        "opportunity_ref": opportunity or request["opportunity_ref"],
        "message_sha256": request["message_sha256"],
        "owner_seat": arbitration["owner_seat"],
        "attempted_at": attempted,
        "outcome": outcome,
        "provider_receipt_sha256": h("provider-" + mid),
    }


class PayoutDeliveryGateTests(unittest.TestCase):
    def compile(self, r=None, a=None, hist=None, when="2026-09-15T02:03:00Z"):
        if r is None:
            r, a, hist = base()
        return m.compile_payout_delivery(r, a, hist, as_of=dt(when))

    def test_ready_and_verifier_and_authority_boundary(self):
        r, a, hist = base()
        receipt = self.compile(r, a, hist)
        self.assertEqual(receipt["state"], m.AUTHORITY_CEILING)
        self.assertTrue(receipt["one_provider_mutation_authorized"])
        for key in (
            "external_action_performed",
            "sponsor_receipt_inferred",
            "sponsor_acceptance_inferred",
            "merge_inferred",
            "settlement_inferred",
            "payment_inferred",
            "cash_inferred",
            "revenue_inferred",
        ):
            self.assertIs(receipt[key], False)
        self.assertEqual(m.verify_payout_delivery_receipt(r, a, hist, receipt), receipt)
        tampered = copy.deepcopy(receipt)
        tampered["payment_inferred"] = True
        with self.assertRaises(m.PayoutDeliveryEvidenceError):
            m.verify_payout_delivery_receipt(r, a, hist, tampered)

    def test_confirmed_and_ambiguous_prior_send_block(self):
        for outcome in ("CONFIRMED_SENT", "AMBIGUOUS"):
            r, a, hist = base()
            hist["items"] = [mutation(r, a, outcome=outcome)]
            receipt = self.compile(r, a, hist)
            self.assertEqual(receipt["state"], "DUPLICATE_SEND_BLOCKED")
            self.assertFalse(receipt["one_provider_mutation_authorized"])

    def test_confirmed_not_sent_does_not_block(self):
        r, a, hist = base()
        hist["items"] = [mutation(r, a, outcome="CONFIRMED_NOT_SENT")]
        self.assertEqual(self.compile(r, a, hist)["state"], m.AUTHORITY_CEILING)

    def test_prior_different_request_same_opportunity_route_blocks(self):
        r, a, hist = base()
        hist["items"] = [mutation(r, a, request_digest=h("old-request"))]
        self.assertEqual(self.compile(r, a, hist)["state"], "DUPLICATE_SEND_BLOCKED")

    def test_different_opportunity_same_route_does_not_block(self):
        r, a, hist = base()
        hist["items"] = [
            mutation(r, a, request_digest=h("other"), opportunity="Scottcjn/Rustchain#999")
        ]
        receipt = self.compile(r, a, hist)
        self.assertEqual(receipt["state"], m.AUTHORITY_CEILING)
        self.assertEqual(receipt["prior_relevant_mutation_count"], 0)

    def test_cross_route_row_rejected(self):
        r, a, hist = base()
        hist["items"] = [mutation(r, a, route=h("other-route"))]
        with self.assertRaisesRegex(m.PayoutDeliveryEvidenceError, "another provider route"):
            self.compile(r, a, hist)

    def test_terminal_states_block(self):
        for state in ("SETTLED_PAID", "SPONSOR_DNR", "OPPORTUNITY_CLOSED"):
            r, a, hist = base()
            hist["terminal"] = {
                "state": state,
                "observed_at": "2026-09-15T02:01:30Z",
                "evidence_sha256": h(state),
            }
            self.assertEqual(self.compile(r, a, hist)["state"], "SETTLED_NO_SEND")

    def test_incomplete_not_selected_revoked(self):
        r, a, hist = base()
        hist["complete"] = False
        self.assertEqual(self.compile(r, a, hist)["state"], "HISTORY_INCOMPLETE_HOLD")
        r, a, hist = base()
        a["decision"] = "NOT_SELECTED"
        self.assertEqual(self.compile(r, a, hist)["state"], "ARBITRATION_REQUIRED")
        r, a, hist = base()
        a["decision"] = "REVOKED"
        self.assertEqual(self.compile(r, a, hist)["state"], "ARBITRATION_REVOKED")

    def test_lease_boundaries(self):
        r, a, hist = base()
        hist["captured_at"] = "2026-09-15T02:00:58Z"
        self.assertEqual(
            self.compile(r, a, hist, "2026-09-15T02:00:59Z")["state"],
            "LEASE_NOT_YET_ACTIVE",
        )
        r, a, hist = base()
        self.assertEqual(
            self.compile(r, a, hist, "2026-09-15T02:11:00Z")["state"], "LEASE_EXPIRED"
        )
        r, a, hist = base()
        self.assertEqual(
            self.compile(r, a, hist, "2026-09-15T02:10:59Z")["state"], m.AUTHORITY_CEILING
        )

    def test_history_lease_and_stale_boundaries(self):
        r, a, hist = base()
        hist["captured_at"] = "2026-09-15T02:00:59Z"
        self.assertEqual(self.compile(r, a, hist)["state"], "HISTORY_PREDATES_LEASE_HOLD")
        r, a, hist = base()
        a["expires_at"] = "2026-09-15T02:30:00Z"
        hist["captured_at"] = "2026-09-15T02:01:00Z"
        self.assertEqual(
            self.compile(r, a, hist, "2026-09-15T02:11:00Z")["state"], m.AUTHORITY_CEILING
        )
        self.assertEqual(
            self.compile(r, a, hist, "2026-09-15T02:11:01Z")["state"], "HISTORY_STALE_HOLD"
        )

    def test_transplants_rejected(self):
        r, a, hist = base()
        a["request_sha256"] = h("wrong")
        with self.assertRaises(m.PayoutDeliveryEvidenceError):
            self.compile(r, a, hist)
        r, a, hist = base()
        a["target_route_sha256"] = h("wrong")
        with self.assertRaises(m.PayoutDeliveryEvidenceError):
            self.compile(r, a, hist)
        r, a, hist = base()
        hist["request_sha256"] = h("wrong")
        with self.assertRaises(m.PayoutDeliveryEvidenceError):
            self.compile(r, a, hist)
        r, a, hist = base()
        hist["target_route_sha256"] = h("wrong")
        with self.assertRaises(m.PayoutDeliveryEvidenceError):
            self.compile(r, a, hist)

    def test_time_evidence_hostiles(self):
        r, a, hist = base()
        r["prepared_at"] = "2026-09-15T02:04:00Z"
        digest = m.request_sha256(r)
        a["request_sha256"] = digest
        hist["request_sha256"] = digest
        with self.assertRaisesRegex(m.PayoutDeliveryEvidenceError, "future"):
            self.compile(r, a, hist)
        r, a, hist = base()
        a["issued_at"] = "2026-09-15T01:59:00Z"
        with self.assertRaisesRegex(m.PayoutDeliveryEvidenceError, "predates"):
            self.compile(r, a, hist)
        r, a, hist = base()
        hist["captured_at"] = "2026-09-15T02:04:00Z"
        with self.assertRaisesRegex(m.PayoutDeliveryEvidenceError, "future"):
            self.compile(r, a, hist)
        r, a, hist = base()
        hist["items"] = [mutation(r, a, attempted="2026-09-15T02:02:01Z")]
        with self.assertRaisesRegex(m.PayoutDeliveryEvidenceError, "postdates"):
            self.compile(r, a, hist)

    def test_duplicates_and_exact_types(self):
        r, a, hist = base()
        row = mutation(r, a)
        hist["items"] = [row, copy.deepcopy(row)]
        with self.assertRaises(m.PayoutDeliveryEvidenceError):
            self.compile(r, a, hist)
        r, a, hist = base()
        hist["complete"] = 1
        with self.assertRaises(m.PayoutDeliveryInputError):
            self.compile(r, a, hist)

    def test_role_and_digest_and_head_validation(self):
        for unsafe in ("buyer@example.com", "https://x.test", "api_key owner", "token=oops"):
            r, a, hist = base()
            r["claimant_label"] = unsafe
            with self.assertRaises(m.PayoutDeliveryInputError):
                self.compile(r, a, hist)
        r, a, hist = base()
        r["work_sha"] = "A" * 40
        with self.assertRaises(m.PayoutDeliveryInputError):
            self.compile(r, a, hist)
        r, a, hist = base()
        r["message_sha256"] = "0" * 63
        with self.assertRaises(m.PayoutDeliveryInputError):
            self.compile(r, a, hist)

    def test_history_order_invariant(self):
        r, a, hist = base()
        first_row = mutation(
            r, a, outcome="CONFIRMED_NOT_SENT", mid="b", attempted="2026-09-15T02:01:40Z"
        )
        second_row = mutation(
            r, a, outcome="CONFIRMED_NOT_SENT", mid="a", attempted="2026-09-15T02:01:20Z"
        )
        hist["items"] = [first_row, second_row]
        first = self.compile(r, a, hist)
        hist["items"] = [second_row, first_row]
        second = self.compile(r, a, hist)
        self.assertEqual(first, second)

    def test_cli_compile_verify_duplicate_keys_and_exclusive_output(self):
        r, a, hist = base()
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            now = datetime.now(timezone.utc).replace(microsecond=0)
            r["prepared_at"] = (now - timedelta(seconds=2)).isoformat().replace("+00:00", "Z")
            digest = m.request_sha256(r)
            a["request_sha256"] = digest
            hist["request_sha256"] = digest
            a["issued_at"] = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
            a["expires_at"] = (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
            hist["captured_at"] = now.isoformat().replace("+00:00", "Z")
            for name, value in (("r", r), ("a", a), ("h", hist)):
                (tmp / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
            output = tmp / "receipt.json"
            command = [
                sys.executable,
                str(MODULE_PATH),
                "compile",
                str(tmp / "r.json"),
                str(tmp / "a.json"),
                str(tmp / "h.json"),
                "--output",
                str(output),
            ]
            completed = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            again = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertNotEqual(again.returncode, 0)
            self.assertIn("refusing to overwrite", again.stderr)
            self.assertNotIn("Traceback", again.stderr)
            verified = tmp / "verified.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "verify",
                    str(tmp / "r.json"),
                    str(tmp / "a.json"),
                    str(tmp / "h.json"),
                    str(output),
                    "--output",
                    str(verified),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(output.read_text()), json.loads(verified.read_text()))
            duplicate = tmp / "dup.json"
            duplicate.write_text('{"schema":"x","schema":"x"}', encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "compile",
                    str(duplicate),
                    str(tmp / "a.json"),
                    str(tmp / "h.json"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("duplicate JSON key", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)
            target = tmp / "target"
            target.write_text("x", encoding="utf-8")
            link = tmp / "link"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                return
            completed = subprocess.run(
                command[:-1] + [str(link)], text=True, capture_output=True, check=False
            )
            self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
