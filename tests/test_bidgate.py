# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from concierge.bidgate import BidGateInputError, main, qualify_bid


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _evidence(
    evidence_id: str,
    evidence_type: str,
    value: str | int | bool,
    *,
    observed_at: str = "2026-09-13T12:00:00+00:00",
    expires_at: str | None = None,
) -> dict:
    item = {
        "id": evidence_id,
        "type": evidence_type,
        "value": value,
        "source": f"fixture://{evidence_id}",
        "observed_at": observed_at,
        "provenance_sha256": _sha(evidence_id),
    }
    if expires_at is not None:
        item["expires_at"] = expires_at
    return item


def _manifest() -> dict:
    return {
        "opportunity": {
            "id": "RFP-EXAMPLE-01",
            "title": "Synthetic public-sector workflow RFP",
            "source": "fixture://rfp-example-01",
            "decision_time": "2026-09-13T13:00:00+00:00",
            "question_deadline": "2026-09-14T15:00:00+00:00",
            "proposal_deadline": "2026-09-18T20:00:00+00:00",
            "contact_policy": "questions only through named procurement contact",
            "minimum_score": 5,
            "requirements": [
                {
                    "id": "insurance",
                    "level": "mandatory",
                    "authority": "prime_only",
                    "evidence_type": "insurance_ready",
                    "rule": {"op": "equals", "value": True},
                    "question": "May insurance be bound after notice of intent to award?",
                },
                {
                    "id": "references",
                    "level": "mandatory",
                    "authority": "partner_allowed",
                    "evidence_type": "comparable_references",
                    "rule": {"op": "gte", "value": 3},
                },
                {
                    "id": "technical_fit",
                    "level": "scored",
                    "authority": "prime_only",
                    "evidence_type": "technical_fit",
                    "rule": {"op": "equals", "value": True},
                    "weight": 5,
                },
            ],
        },
        "participants": [
            {
                "id": "tjlabs",
                "role": "prime",
                "evidence": [
                    _evidence("ev-insurance", "insurance_ready", True),
                    _evidence("ev-references", "comparable_references", 4),
                    _evidence("ev-fit", "technical_fit", True),
                ],
            }
        ],
    }


class BidGateTests(unittest.TestCase):
    def test_prime_ready_when_prime_satisfies_every_gate(self) -> None:
        result = qualify_bid(_manifest())
        self.assertEqual("PRIME_READY", result["disposition"])
        self.assertEqual(5, result["score"])
        self.assertEqual([], result["blockers"])
        self.assertEqual("PREPARE_PROPOSAL_FOR_HUMAN_APPROVAL", result["safe_next_action"])
        self.assertTrue(all(value is False for value in result["authority"].values()))

    def test_partner_evidence_yields_team_required_only_when_allowed(self) -> None:
        manifest = _manifest()
        manifest["participants"][0]["evidence"] = [
            item
            for item in manifest["participants"][0]["evidence"]
            if item["type"] != "comparable_references"
        ]
        manifest["participants"].append(
            {
                "id": "qualified-prime-partner",
                "role": "partner",
                "evidence": [_evidence("partner-refs", "comparable_references", 8)],
            }
        )
        result = qualify_bid(manifest)
        self.assertEqual("TEAM_REQUIRED", result["disposition"])
        self.assertEqual(["references"], result["partner_satisfied_requirements"])
        self.assertEqual(
            "PREPARE_TEAMING_PACKAGE_FOR_HUMAN_APPROVAL", result["safe_next_action"]
        )

    def test_partner_cannot_satisfy_prime_only_gate(self) -> None:
        manifest = _manifest()
        manifest["participants"][0]["evidence"] = [
            item
            for item in manifest["participants"][0]["evidence"]
            if item["type"] != "insurance_ready"
        ]
        manifest["participants"].append(
            {
                "id": "partner",
                "role": "partner",
                "evidence": [_evidence("partner-insurance", "insurance_ready", True)],
            }
        )
        result = qualify_bid(manifest)
        self.assertEqual("NO_GO", result["disposition"])
        self.assertIn(
            ("insurance", "MANDATORY_GATE_UNSATISFIED"),
            {(item["requirement_id"], item["code"]) for item in result["blockers"]},
        )

    def test_scored_strength_cannot_override_mandatory_gap(self) -> None:
        manifest = _manifest()
        manifest["participants"][0]["evidence"] = [
            item
            for item in manifest["participants"][0]["evidence"]
            if item["type"] != "insurance_ready"
        ]
        result = qualify_bid(manifest)
        self.assertEqual(5, result["score"])
        self.assertEqual("NO_GO", result["disposition"])

    def test_expired_only_evidence_holds_instead_of_inventing_no_go(self) -> None:
        manifest = _manifest()
        for item in manifest["participants"][0]["evidence"]:
            if item["type"] == "insurance_ready":
                item["expires_at"] = "2026-09-13T12:30:00+00:00"
        result = qualify_bid(manifest)
        self.assertEqual("HOLD", result["disposition"])
        self.assertIn(
            ("insurance", "EXPIRED_EVIDENCE"),
            {(item["requirement_id"], item["code"]) for item in result["hold_reasons"]},
        )
        self.assertEqual(
            "HOLD_FOR_EVIDENCE_OR_CLARIFICATION", result["safe_next_action"]
        )

    def test_conflicting_current_evidence_holds(self) -> None:
        manifest = _manifest()
        manifest["participants"][0]["evidence"].append(
            _evidence("ev-insurance-conflict", "insurance_ready", False)
        )
        result = qualify_bid(manifest)
        self.assertEqual("HOLD", result["disposition"])
        self.assertIn(
            "AMBIGUOUS_EVIDENCE", {item["code"] for item in result["hold_reasons"]}
        )

    def test_duplicate_ids_are_rejected(self) -> None:
        manifest = _manifest()
        duplicate = copy.deepcopy(manifest["participants"][0]["evidence"][0])
        manifest["participants"][0]["evidence"].append(duplicate)
        with self.assertRaisesRegex(BidGateInputError, "evidence ids must be unique"):
            qualify_bid(manifest)

    def test_future_observation_is_rejected(self) -> None:
        manifest = _manifest()
        manifest["participants"][0]["evidence"][0]["observed_at"] = (
            "2026-09-13T14:00:00+00:00"
        )
        with self.assertRaisesRegex(BidGateInputError, "cannot be after decision_time"):
            qualify_bid(manifest)

    def test_non_integer_score_weight_is_rejected(self) -> None:
        manifest = _manifest()
        manifest["opportunity"]["requirements"][2]["weight"] = float("nan")
        with self.assertRaisesRegex(BidGateInputError, "weight must be an integer"):
            qualify_bid(manifest)

    def test_minimum_score_is_enforced_after_mandatory_gates(self) -> None:
        manifest = _manifest()
        manifest["participants"][0]["evidence"] = [
            item
            for item in manifest["participants"][0]["evidence"]
            if item["type"] != "technical_fit"
        ]
        result = qualify_bid(manifest)
        self.assertEqual("NO_GO", result["disposition"])
        self.assertIn("MINIMUM_SCORE_UNMET", {item["code"] for item in result["blockers"]})

    def test_question_is_emitted_only_before_question_deadline(self) -> None:
        manifest = _manifest()
        manifest["participants"][0]["evidence"] = [
            item
            for item in manifest["participants"][0]["evidence"]
            if item["type"] != "insurance_ready"
        ]
        result = qualify_bid(manifest)
        self.assertEqual("insurance", result["clarification_questions"][0]["requirement_id"])
        self.assertEqual(
            "questions only through named procurement contact", result["contact_policy"]
        )

        manifest["opportunity"]["decision_time"] = "2026-09-15T13:00:00+00:00"
        for item in manifest["participants"][0]["evidence"]:
            item["observed_at"] = "2026-09-15T12:00:00+00:00"
        result = qualify_bid(manifest)
        self.assertEqual([], result["clarification_questions"])

    def test_proposal_deadline_is_terminal_no_go(self) -> None:
        manifest = _manifest()
        manifest["opportunity"]["decision_time"] = "2026-09-18T20:00:00+00:00"
        for item in manifest["participants"][0]["evidence"]:
            item["observed_at"] = "2026-09-18T19:00:00+00:00"
        result = qualify_bid(manifest)
        self.assertEqual("NO_GO", result["disposition"])
        self.assertIn(
            "PROPOSAL_DEADLINE_PASSED", {item["code"] for item in result["blockers"]}
        )

    def test_evidence_substitution_changes_receipt(self) -> None:
        first = qualify_bid(_manifest())
        manifest = _manifest()
        manifest["participants"][0]["evidence"][0]["source"] = "fixture://other-source"
        second = qualify_bid(manifest)
        self.assertNotEqual(first["evidence_digest"], second["evidence_digest"])
        self.assertNotEqual(first["decision_digest"], second["decision_digest"])

    def test_requirement_substitution_changes_receipt(self) -> None:
        first = qualify_bid(_manifest())
        manifest = _manifest()
        manifest["opportunity"]["contact_policy"] = "portal questions only"
        second = qualify_bid(manifest)
        self.assertNotEqual(first["opportunity_digest"], second["opportunity_digest"])
        self.assertNotEqual(first["decision_digest"], second["decision_digest"])

    def test_replay_is_byte_deterministic(self) -> None:
        first = qualify_bid(_manifest())
        second = qualify_bid(copy.deepcopy(_manifest()))
        self.assertEqual(
            json.dumps(first, sort_keys=True, separators=(",", ":")),
            json.dumps(second, sort_keys=True, separators=(",", ":")),
        )

    def test_cli_returns_disposition_exit_codes_without_echoing_manifest(self) -> None:
        manifest = _manifest()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main([str(path)])
            self.assertEqual(0, code)
            self.assertEqual("PRIME_READY", json.loads(stdout.getvalue())["disposition"])

            path.write_text("not-json", encoding="utf-8")
            stderr = StringIO()
            with redirect_stderr(stderr):
                code = main([str(path)])
            self.assertEqual(64, code)
            self.assertIn("bidgate input error", stderr.getvalue())
            self.assertNotIn("not-json", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
