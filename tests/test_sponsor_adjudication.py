import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from concierge import sponsor_adjudication as sa


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()


def manifest():
    return {
        "schema_version": 1,
        "program": {
            "program_id": "synthetic-bounty",
            "sponsor": "Synthetic Sponsor",
            "source_ref": "https://example.invalid/program",
        },
        "findings": [
            {"finding_id": "f-1", "fingerprint_sha256": h("f1"), "title": "Fail-open parser", "submitted_by": "tester", "evidence_ref": "repo#1"},
            {"finding_id": "f-2", "fingerprint_sha256": h("f2"), "title": "Quota poisoning", "submitted_by": "tester", "evidence_ref": "repo#2"},
            {"finding_id": "f-3", "fingerprint_sha256": h("f3"), "title": "Amplification", "submitted_by": "tester", "evidence_ref": "repo#3"},
        ],
        "submissions": [
            {"submission_id": "s-1", "finding_ids": ["f-1", "f-2"], "submitted_at": "2026-09-13T20:00:00Z", "channel": "email", "receipt_ref": "mail:m1", "receipt_sha256": h("m1")},
            {"submission_id": "s-2", "finding_ids": ["f-3"], "submitted_at": "2026-09-13T20:01:00Z", "channel": "email", "receipt_ref": "mail:m2", "receipt_sha256": h("m2")},
        ],
        "sponsor_events": [
            {
                "event_id": "e-1", "event_type": "SPONSOR_VERIFIED", "event_at": "2026-09-13T21:00:00Z",
                "source_ref": "mail:sponsor-1", "source_sha256": h("sponsor-1"), "claim_unit_id": "unit-1",
                "finding_ids": ["f-1", "f-2", "f-3"], "submission_ids": ["s-1", "s-2"],
                "note": "Concrete findings verified; reward breakdown pending",
            },
            {
                "event_id": "e-2", "event_type": "ADJUDICATION_STARTED", "event_at": "2026-09-13T21:01:00Z",
                "source_ref": "mail:sponsor-1", "source_sha256": h("sponsor-1"), "claim_unit_id": "unit-1",
            },
        ],
    }


class SponsorAdjudicationTests(unittest.TestCase):
    def test_real_world_shape_collapses_many_findings_to_one_unit_without_cash(self):
        report = sa.compile_manifest(manifest())
        self.assertEqual(report["summary"]["finding_count"], 3)
        self.assertEqual(report["summary"]["claim_unit_count"], 1)
        unit = report["claim_units"][0]
        self.assertEqual(unit["status"], "adjudicating")
        self.assertTrue(unit["sponsor_verified"])
        self.assertEqual(unit["action"], "WAIT_SPONSOR")
        self.assertEqual(unit["cash_status"], "not_inferred")
        self.assertFalse(report["summary"]["cash_recognized"])
        self.assertTrue(all(row["action"] == "DO_NOT_RESUBMIT" for row in report["findings"]))

    def test_exact_recompile_is_deterministic(self):
        self.assertEqual(sa.build_artifacts(manifest()), sa.build_artifacts(copy.deepcopy(manifest())))

    def test_output_receipt_and_artifacts_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            sa.write_artifacts(manifest(), tmp)
            receipt = sa.verify_artifacts(tmp)
            self.assertEqual(receipt["kind"], "sponsor_adjudication_custody_receipt")

    def test_tampered_markdown_fails_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            sa.write_artifacts(manifest(), tmp)
            Path(tmp, "adjudication.md").write_text("tampered", encoding="utf-8")
            with self.assertRaises(sa.AdjudicationError):
                sa.verify_artifacts(tmp)

    def test_tampered_report_authority_fails_even_if_digest_stale(self):
        report = sa.compile_manifest(manifest())
        report["authority_ceiling"]["payment_recognition"] = True
        with self.assertRaises(sa.AdjudicationError):
            sa.verify_report(report)

    def test_duplicate_json_keys_fail_closed(self):
        with self.assertRaises(sa.AdjudicationError):
            sa.loads_strict('{"schema_version":1,"schema_version":1}')

    def test_float_json_fails_closed(self):
        with self.assertRaises(sa.AdjudicationError):
            sa.loads_strict('{"x":1.2}')

    def test_nonfinite_json_fails_closed(self):
        with self.assertRaises(sa.AdjudicationError):
            sa.loads_strict('{"x":NaN}')

    def test_duplicate_event_id_fails(self):
        m = manifest()
        m["sponsor_events"].append(copy.deepcopy(m["sponsor_events"][0]))
        with self.assertRaisesRegex(sa.AdjudicationError, "duplicate sponsor event_id"):
            sa.compile_manifest(m)

    def test_event_cannot_predate_submission(self):
        m = manifest()
        m["sponsor_events"][0]["event_at"] = "2026-09-13T19:59:59Z"
        with self.assertRaisesRegex(sa.AdjudicationError, "predates"):
            sa.compile_manifest(m)

    def test_same_unit_events_need_strictly_increasing_time(self):
        m = manifest()
        m["sponsor_events"][1]["event_at"] = m["sponsor_events"][0]["event_at"]
        with self.assertRaisesRegex(sa.AdjudicationError, "strictly increasing"):
            sa.compile_manifest(m)

    def test_finding_cannot_be_silently_rebound(self):
        m = manifest()
        m["sponsor_events"] = [m["sponsor_events"][0]]
        second = copy.deepcopy(m["sponsor_events"][0])
        second.update({"event_id": "e-x", "event_at": "2026-09-13T21:02:00Z", "claim_unit_id": "unit-x", "finding_ids": ["f-1"], "submission_ids": ["s-1"]})
        m["sponsor_events"].append(second)
        with self.assertRaisesRegex(sa.AdjudicationError, "explicit collapse required"):
            sa.compile_manifest(m)

    def test_explicit_collapse_marks_source_duplicate_and_moves_findings(self):
        m = manifest()
        m["sponsor_events"] = [
            {"event_id": "a", "event_type": "SPONSOR_RECEIVED", "event_at": "2026-09-13T21:00:00Z", "source_ref": "mail:a", "source_sha256": h("a"), "claim_unit_id": "u-a", "finding_ids": ["f-1"], "submission_ids": ["s-1"]},
            {"event_id": "b", "event_type": "SPONSOR_RECEIVED", "event_at": "2026-09-13T21:00:01Z", "source_ref": "mail:b", "source_sha256": h("b"), "claim_unit_id": "u-b", "finding_ids": ["f-2", "f-3"], "submission_ids": ["s-1", "s-2"]},
            {"event_id": "c", "event_type": "DUPLICATE_COLLAPSED", "event_at": "2026-09-13T21:01:00Z", "source_ref": "mail:c", "source_sha256": h("c"), "claim_unit_id": "u-a", "collapsed_claim_unit_ids": ["u-b"]},
        ]
        report = sa.compile_manifest(m)
        units = {u["claim_unit_id"]: u for u in report["claim_units"]}
        self.assertEqual(units["u-b"]["status"], "duplicate")
        self.assertEqual(units["u-b"]["collapsed_into"], "u-a")
        self.assertEqual(units["u-a"]["finding_ids"], ["f-1", "f-2", "f-3"])
        self.assertEqual(next(x for x in report["findings"] if x["finding_id"] == "f-3")["canonical_claim_unit_id"], "u-a")


    def test_collapse_preserves_verified_authority(self):
        m = manifest()
        m["sponsor_events"] = [
            {"event_id": "a", "event_type": "SPONSOR_RECEIVED", "event_at": "2026-09-13T21:00:00Z", "source_ref": "mail:a", "source_sha256": h("a"), "claim_unit_id": "u-a", "finding_ids": ["f-1"], "submission_ids": ["s-1"]},
            {"event_id": "b", "event_type": "SPONSOR_VERIFIED", "event_at": "2026-09-13T21:00:01Z", "source_ref": "mail:b", "source_sha256": h("b"), "claim_unit_id": "u-b", "finding_ids": ["f-2", "f-3"], "submission_ids": ["s-1", "s-2"]},
            {"event_id": "c", "event_type": "DUPLICATE_COLLAPSED", "event_at": "2026-09-13T21:01:00Z", "source_ref": "mail:c", "source_sha256": h("c"), "claim_unit_id": "u-a", "collapsed_claim_unit_ids": ["u-b"]},
        ]
        units = {u["claim_unit_id"]: u for u in sa.compile_manifest(m)["claim_units"]}
        self.assertTrue(units["u-a"]["sponsor_verified"])
        self.assertEqual(units["u-a"]["status"], "sponsor_verified")

    def test_adjudication_event_cannot_downgrade_reward_offer(self):
        m = manifest()
        m["sponsor_events"].extend([
            {"event_id": "e-3", "event_type": "REWARD_OFFERED", "event_at": "2026-09-13T21:02:00Z", "source_ref": "mail:offer", "source_sha256": h("offer"), "claim_unit_id": "unit-1", "amount": "90", "currency": "USD"},
            {"event_id": "e-4", "event_type": "ADJUDICATION_STARTED", "event_at": "2026-09-13T21:03:00Z", "source_ref": "mail:late", "source_sha256": h("late"), "claim_unit_id": "unit-1"},
        ])
        with self.assertRaisesRegex(sa.AdjudicationError, "cannot follow a reward offer"):
            sa.compile_manifest(m)

    def test_collapse_after_reward_offer_is_rejected(self):
        m = manifest()
        m["sponsor_events"] = [
            {"event_id": "a", "event_type": "SPONSOR_RECEIVED", "event_at": "2026-09-13T21:00:00Z", "source_ref": "mail:a", "source_sha256": h("a"), "claim_unit_id": "u-a", "finding_ids": ["f-1"], "submission_ids": ["s-1"]},
            {"event_id": "b", "event_type": "SPONSOR_RECEIVED", "event_at": "2026-09-13T21:00:01Z", "source_ref": "mail:b", "source_sha256": h("b"), "claim_unit_id": "u-b", "finding_ids": ["f-2"], "submission_ids": ["s-1"]},
            {"event_id": "offer", "event_type": "REWARD_OFFERED", "event_at": "2026-09-13T21:00:02Z", "source_ref": "mail:o", "source_sha256": h("o"), "claim_unit_id": "u-b", "amount": "90.00", "currency": "USD"},
            {"event_id": "c", "event_type": "DUPLICATE_COLLAPSED", "event_at": "2026-09-13T21:01:00Z", "source_ref": "mail:c", "source_sha256": h("c"), "claim_unit_id": "u-a", "collapsed_claim_unit_ids": ["u-b"]},
        ]
        with self.assertRaisesRegex(sa.AdjudicationError, "already has reward authority"):
            sa.compile_manifest(m)

    def test_reward_offer_does_not_become_cash(self):
        m = manifest()
        m["sponsor_events"].append({"event_id": "e-3", "event_type": "REWARD_OFFERED", "event_at": "2026-09-13T21:02:00Z", "source_ref": "mail:offer", "source_sha256": h("offer"), "claim_unit_id": "unit-1", "amount": "90.00", "currency": "USD"})
        report = sa.compile_manifest(m)
        unit = report["claim_units"][0]
        self.assertEqual(unit["status"], "reward_offered")
        self.assertEqual(unit["reward_amount"], "90.00")
        self.assertEqual(unit["cash_status"], "not_inferred")
        self.assertEqual(unit["action"], "OWNER_REVIEW")

    def test_payment_report_is_only_paid_evidence_pending(self):
        m = manifest()
        m["sponsor_events"].extend([
            {"event_id": "e-3", "event_type": "REWARD_OFFERED", "event_at": "2026-09-13T21:02:00Z", "source_ref": "mail:offer", "source_sha256": h("offer"), "claim_unit_id": "unit-1", "amount": "90.00", "currency": "USD"},
            {"event_id": "e-4", "event_type": "PAYMENT_REPORTED", "event_at": "2026-09-13T21:03:00Z", "source_ref": "mail:sent", "source_sha256": h("sent"), "claim_unit_id": "unit-1", "amount": "90.00", "currency": "USD"},
        ])
        unit = sa.compile_manifest(m)["claim_units"][0]
        self.assertEqual(unit["status"], "paid_evidence_pending")
        self.assertEqual(unit["cash_status"], "not_inferred")

    def test_payment_report_without_offer_fails(self):
        m = manifest()
        m["sponsor_events"].append({"event_id": "e-3", "event_type": "PAYMENT_REPORTED", "event_at": "2026-09-13T21:02:00Z", "source_ref": "mail:sent", "source_sha256": h("sent"), "claim_unit_id": "unit-1", "amount": "90", "currency": "USD"})
        with self.assertRaisesRegex(sa.AdjudicationError, "requires prior reward"):
            sa.compile_manifest(m)

    def test_payment_report_amount_must_match_offer(self):
        m = manifest()
        m["sponsor_events"].extend([
            {"event_id": "e-3", "event_type": "REWARD_OFFERED", "event_at": "2026-09-13T21:02:00Z", "source_ref": "mail:offer", "source_sha256": h("offer"), "claim_unit_id": "unit-1", "amount": "90", "currency": "USD"},
            {"event_id": "e-4", "event_type": "PAYMENT_REPORTED", "event_at": "2026-09-13T21:03:00Z", "source_ref": "mail:sent", "source_sha256": h("sent"), "claim_unit_id": "unit-1", "amount": "91", "currency": "USD"},
        ])
        with self.assertRaisesRegex(sa.AdjudicationError, "must equal"):
            sa.compile_manifest(m)

    def test_declined_is_terminal_do_not_resubmit(self):
        m = manifest()
        m["sponsor_events"].append({"event_id": "e-3", "event_type": "DECLINED", "event_at": "2026-09-13T21:02:00Z", "source_ref": "mail:no", "source_sha256": h("no"), "claim_unit_id": "unit-1"})
        unit = sa.compile_manifest(m)["claim_units"][0]
        self.assertEqual((unit["status"], unit["action"]), ("declined", "DO_NOT_RESUBMIT"))

    def test_terminal_unit_cannot_advance(self):
        m = manifest()
        m["sponsor_events"].extend([
            {"event_id": "e-3", "event_type": "DECLINED", "event_at": "2026-09-13T21:02:00Z", "source_ref": "mail:no", "source_sha256": h("no"), "claim_unit_id": "unit-1"},
            {"event_id": "e-4", "event_type": "ADJUDICATION_STARTED", "event_at": "2026-09-13T21:03:00Z", "source_ref": "mail:again", "source_sha256": h("again"), "claim_unit_id": "unit-1"},
        ])
        with self.assertRaisesRegex(sa.AdjudicationError, "terminal unit"):
            sa.compile_manifest(m)

    def test_unanswered_submission_stays_owner_review(self):
        m = manifest()
        m["sponsor_events"] = []
        report = sa.compile_manifest(m)
        self.assertEqual(report["claim_units"], [])
        self.assertTrue(all(row["status"] == "submitted" and row["action"] == "OWNER_REVIEW" for row in report["findings"]))

    def test_duplicate_submissions_are_visible_not_erased(self):
        m = manifest()
        m["submissions"].append({"submission_id": "s-3", "finding_ids": ["f-1"], "submitted_at": "2026-09-13T20:02:00Z", "channel": "email", "receipt_ref": "mail:m3", "receipt_sha256": h("m3")})
        report = sa.compile_manifest(m)
        row = next(r for r in report["findings"] if r["finding_id"] == "f-1")
        self.assertEqual(row["duplicate_submission_count"], 1)

    def test_same_fingerprint_cannot_mint_two_findings(self):
        m = manifest()
        m["findings"][1]["fingerprint_sha256"] = m["findings"][0]["fingerprint_sha256"]
        with self.assertRaisesRegex(sa.AdjudicationError, "same fingerprint"):
            sa.compile_manifest(m)

    def test_unknown_fields_fail_closed(self):
        m = manifest()
        m["sponsor_events"][0]["payment_confirmed"] = True
        with self.assertRaisesRegex(sa.AdjudicationError, "schema mismatch"):
            sa.compile_manifest(m)

    def test_bool_schema_version_fails(self):
        m = manifest()
        m["schema_version"] = True
        with self.assertRaises(sa.AdjudicationError):
            sa.compile_manifest(m)

    def test_noncanonical_reward_amount_fails(self):
        m = manifest()
        m["sponsor_events"].append({"event_id": "e-3", "event_type": "REWARD_OFFERED", "event_at": "2026-09-13T21:02:00Z", "source_ref": "mail:offer", "source_sha256": h("offer"), "claim_unit_id": "unit-1", "amount": "090.00", "currency": "USD"})
        with self.assertRaisesRegex(sa.AdjudicationError, "canonical"):
            sa.compile_manifest(m)

    def test_formula_leading_csv_value_is_neutralized(self):
        m = manifest()
        m["sponsor_events"] = []
        m["findings"][0]["title"] = "=HYPERLINK(1)"
        artifacts = sa.build_artifacts(m)
        # Claim-units CSV deliberately does not export titles; direct helper still guarantees safe cells.
        self.assertEqual(sa._csv_cell("=X"), "'=X")
        self.assertIn(b"claim_unit_id,status,action", artifacts["claim_units.csv"])

    def test_markdown_escapes_table_breakers(self):
        m = manifest()
        m["program"]["sponsor"] = "Sponsor | `name`"
        text = sa.build_artifacts(m)["adjudication.md"].decode()
        self.assertIn("Sponsor \\| \\`name\\`", text)

    def test_cli_compile_and_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest(), separators=(",", ":")), encoding="utf-8")
            out = root / "out"
            p = subprocess.run([sys.executable, "-m", "concierge.sponsor_adjudication", "compile", str(manifest_path), "--out-dir", str(out)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            p2 = subprocess.run([sys.executable, "-m", "concierge.sponsor_adjudication", "verify", str(out)], capture_output=True, text=True)
            self.assertEqual(p2.returncode, 0, p2.stdout + p2.stderr)

    def test_cli_rejects_bad_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "manifest.json"
            manifest_path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
            p = subprocess.run([sys.executable, "-m", "concierge.sponsor_adjudication", "compile", str(manifest_path), "--out-dir", str(root / "out")], capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            self.assertIn('"ok":false', p.stdout)


if __name__ == "__main__":
    unittest.main()
