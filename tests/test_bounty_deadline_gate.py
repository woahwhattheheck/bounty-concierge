from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.bounty_deadline_gate import (
    BountyDeadlineInputError,
    compile_bounty_deadline_gate,
    verify_receipt,
)


H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64


def request(**overrides):
    payload = {
        "schema": "bounty-deadline-gate/v1",
        "issue": {
            "url": "https://github.com/example/project/issues/5",
            "state": "OPEN",
            "observed_at": "2026-09-19T23:50:00Z",
            "source_content_sha256": H1,
        },
        "deadline_evidence": {
            "kind": "DATE",
            "value": "2026-09-25",
            "authority": "ISSUE_BODY",
            "source_url": "https://github.com/example/project/issues/5",
            "source_content_sha256": H2,
            "observed_at": "2026-09-19T23:50:00Z",
        },
        "extension_evidence": None,
        "evaluated_at": "2026-09-19T23:55:00Z",
        "max_observation_age_seconds": 900,
    }
    payload.update(overrides)
    return payload


def extension(**overrides):
    value = {
        "kind": "DATE",
        "value": "2026-10-01",
        "authority": "REPO_OWNER",
        "source_url": "https://github.com/example/project/issues/5",
        "source_content_sha256": H3,
        "observed_at": "2026-09-19T23:52:00Z",
    }
    value.update(overrides)
    return value


class BountyDeadlineGateTests(unittest.TestCase):
    def test_future_date_deadline_is_current_but_advisory_only(self):
        receipt = compile_bounty_deadline_gate(request())
        self.assertEqual(receipt["disposition"], "DEADLINE_CURRENT")
        self.assertEqual(receipt["reason_codes"], [])
        self.assertFalse(receipt["authority"]["repository_write_authority"])
        self.assertFalse(receipt["authority"]["reward_award_authority"])

    def test_open_issue_after_date_deadline_holds(self):
        payload = request()
        payload["deadline_evidence"] = {
            **payload["deadline_evidence"],
            "value": "2026-09-16",
        }
        receipt = compile_bounty_deadline_gate(payload)
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("SPONSOR_DEADLINE_ELAPSED", receipt["reason_codes"])
        self.assertEqual(
            receipt["advisory_next_action"],
            "REQUIRE_EXPLICIT_SPONSOR_EXTENSION_BEFORE_LABOR",
        )

    def test_open_provider_wording_is_not_implicit_extension(self):
        payload = request()
        payload["deadline_evidence"] = {
            **payload["deadline_evidence"],
            "value": "2026-08-31",
        }
        receipt = compile_bounty_deadline_gate(payload)
        self.assertIn("SPONSOR_DEADLINE_ELAPSED", receipt["reason_codes"])
        self.assertEqual(receipt["deadline"]["extension_status"], "NONE")

    def test_date_only_deadline_near_boundary_holds_for_timezone_and_clock_ambiguity(self):
        for evaluated_at, observed_at in (
            ("2026-09-24T12:00:00Z", "2026-09-24T11:58:00Z"),
            ("2026-09-25T12:00:00Z", "2026-09-25T11:58:00Z"),
            ("2026-09-26T12:00:00Z", "2026-09-26T11:58:00Z"),
        ):
            with self.subTest(evaluated_at=evaluated_at):
                payload = request(evaluated_at=evaluated_at)
                payload["issue"] = {**payload["issue"], "observed_at": observed_at}
                payload["deadline_evidence"] = {
                    **payload["deadline_evidence"],
                    "observed_at": observed_at,
                }
                receipt = compile_bounty_deadline_gate(payload)
                self.assertEqual(receipt["disposition"], "HOLD")
                self.assertIn("DATE_BOUNDARY_CLOCK_AMBIGUOUS", receipt["reason_codes"])

    def test_date_only_deadline_two_days_ahead_is_unambiguously_current(self):
        payload = request(evaluated_at="2026-09-23T12:00:00Z")
        payload["issue"] = {**payload["issue"], "observed_at": "2026-09-23T11:58:00Z"}
        payload["deadline_evidence"] = {
            **payload["deadline_evidence"],
            "observed_at": "2026-09-23T11:58:00Z",
        }
        receipt = compile_bounty_deadline_gate(payload)
        self.assertEqual(receipt["disposition"], "DEADLINE_CURRENT")

    def test_date_only_deadline_two_days_past_is_unambiguously_elapsed(self):
        payload = request(evaluated_at="2026-09-27T12:00:00Z")
        payload["issue"] = {**payload["issue"], "observed_at": "2026-09-27T11:58:00Z"}
        payload["deadline_evidence"] = {
            **payload["deadline_evidence"],
            "observed_at": "2026-09-27T11:58:00Z",
        }
        receipt = compile_bounty_deadline_gate(payload)
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("SPONSOR_DEADLINE_ELAPSED", receipt["reason_codes"])

    def test_instant_deadline_is_exact_at_boundary(self):
        payload = request(evaluated_at="2026-09-25T12:00:00Z")
        payload["issue"] = {
            **payload["issue"],
            "observed_at": "2026-09-25T11:58:00Z",
        }
        payload["deadline_evidence"] = {
            **payload["deadline_evidence"],
            "kind": "INSTANT",
            "value": "2026-09-25T12:00:00Z",
            "observed_at": "2026-09-25T11:58:00Z",
        }
        receipt = compile_bounty_deadline_gate(payload)
        self.assertEqual(receipt["disposition"], "DEADLINE_CURRENT")
        self.assertEqual(receipt["deadline"]["relation_at_evaluation"], "CURRENT")

    def test_valid_owner_extension_can_restore_current_deadline(self):
        payload = request()
        payload["deadline_evidence"] = {
            **payload["deadline_evidence"],
            "value": "2026-09-16",
        }
        payload["extension_evidence"] = extension()
        receipt = compile_bounty_deadline_gate(payload)
        self.assertEqual(receipt["disposition"], "DEADLINE_CURRENT")
        self.assertEqual(receipt["deadline"]["extension_status"], "ACCEPTED")
        self.assertEqual(receipt["deadline"]["effective_value"], "2026-10-01")

    def test_weak_extension_authority_rejected_at_input_boundary(self):
        payload = request()
        payload["extension_evidence"] = extension(authority="ISSUE_BODY")
        with self.assertRaisesRegex(BountyDeadlineInputError, "extension_evidence.authority"):
            compile_bounty_deadline_gate(payload)

    def test_retrograde_extension_holds_and_does_not_replace_base(self):
        payload = request()
        payload["deadline_evidence"] = {
            **payload["deadline_evidence"],
            "value": "2026-09-25",
        }
        payload["extension_evidence"] = extension(value="2026-09-24")
        receipt = compile_bounty_deadline_gate(payload)
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn(
            "EXTENSION_NOT_STRICTLY_LATER_SAME_PRECISION", receipt["reason_codes"]
        )
        self.assertEqual(receipt["deadline"]["effective_value"], "2026-09-25")

    def test_cross_precision_extension_is_not_compared_by_invented_timezone(self):
        payload = request()
        payload["extension_evidence"] = extension(
            kind="INSTANT", value="2026-10-01T23:59:59Z"
        )
        receipt = compile_bounty_deadline_gate(payload)
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn(
            "EXTENSION_NOT_STRICTLY_LATER_SAME_PRECISION", receipt["reason_codes"]
        )

    def test_future_observation_is_rejected(self):
        payload = request()
        payload["extension_evidence"] = extension(observed_at="2026-09-20T00:00:00Z")
        with self.assertRaisesRegex(BountyDeadlineInputError, "must not be after"):
            compile_bounty_deadline_gate(payload)

    def test_stale_issue_and_deadline_observations_hold(self):
        payload = request(
            evaluated_at="2026-09-20T00:20:01Z",
            max_observation_age_seconds=1800,
        )
        receipt = compile_bounty_deadline_gate(payload)
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("ISSUE_OBSERVATION_STALE", receipt["reason_codes"])
        self.assertIn("DEADLINE_OBSERVATION_STALE", receipt["reason_codes"])

    def test_stale_extension_never_unlocks_elapsed_base(self):
        payload = request(
            evaluated_at="2026-09-20T00:20:01Z",
            max_observation_age_seconds=1800,
        )
        payload["issue"] = {
            **payload["issue"],
            "observed_at": "2026-09-20T00:19:00Z",
        }
        payload["deadline_evidence"] = {
            **payload["deadline_evidence"],
            "value": "2026-09-16",
            "observed_at": "2026-09-20T00:19:00Z",
        }
        payload["extension_evidence"] = extension(
            observed_at="2026-09-19T23:00:00Z"
        )
        receipt = compile_bounty_deadline_gate(payload)
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("EXTENSION_OBSERVATION_STALE", receipt["reason_codes"])
        self.assertIn("SPONSOR_DEADLINE_ELAPSED", receipt["reason_codes"])
        self.assertEqual(receipt["deadline"]["extension_status"], "REJECTED_STALE")

    def test_closed_issue_holds_even_with_future_deadline(self):
        payload = request()
        payload["issue"] = {**payload["issue"], "state": "CLOSED"}
        receipt = compile_bounty_deadline_gate(payload)
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("CANONICAL_ISSUE_NOT_OPEN", receipt["reason_codes"])

    def test_unknown_keys_rejected_to_prevent_semantic_smuggling(self):
        payload = request(extra_authority=True)
        with self.assertRaisesRegex(BountyDeadlineInputError, "unknown keys"):
            compile_bounty_deadline_gate(payload)

    def test_url_aliases_and_encoded_paths_rejected(self):
        payload = request()
        for bad in (
            "https://github.com/example/project/issues/5?x=1",
            "https://user@github.com/example/project/issues/5",
            "https://github.com:443/example/project/issues/5",
            "https://github.com/example/project/issues/%35",
        ):
            with self.subTest(url=bad):
                candidate = deepcopy(payload)
                candidate["issue"]["url"] = bad
                with self.assertRaises(BountyDeadlineInputError):
                    compile_bounty_deadline_gate(candidate)

    def test_receipt_is_deterministic_and_semantically_tamper_evident(self):
        first = compile_bounty_deadline_gate(request())
        second = compile_bounty_deadline_gate(request())
        self.assertEqual(first, second)
        self.assertTrue(verify_receipt(first))

        forged = deepcopy(first)
        forged["disposition"] = "DEADLINE_CURRENT" if first["disposition"] == "HOLD" else "HOLD"
        body = dict(forged)
        body.pop("receipt_sha256", None)
        forged["receipt_sha256"] = hashlib.sha256(
            json.dumps(
                body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
        self.assertTrue(verify_receipt(forged, semantic=False))
        self.assertFalse(verify_receipt(forged, semantic=True))

    def test_authority_forgery_rejected_even_if_rehashed(self):
        receipt = compile_bounty_deadline_gate(request())
        forged = deepcopy(receipt)
        forged["authority"]["reward_award_authority"] = True
        body = dict(forged)
        body.pop("receipt_sha256", None)
        forged["receipt_sha256"] = hashlib.sha256(
            json.dumps(
                body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
        self.assertFalse(verify_receipt(forged, semantic=False))

    def test_cli_compile_and_verify_round_trip(self):
        payload = request()
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            receipt_path = Path(tmp) / "receipt.json"
            snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
            compiled = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "concierge.bounty_deadline_gate",
                    "compile",
                    str(snapshot_path),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            receipt_path.write_text(compiled.stdout, encoding="utf-8")
            verified = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "concierge.bounty_deadline_gate",
                    "verify",
                    str(receipt_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(verified.stdout.strip(), "VALID")

    def test_cli_hold_returns_two_for_elapsed_offer(self):
        payload = request()
        payload["deadline_evidence"] = {
            **payload["deadline_evidence"],
            "value": "2026-09-16",
        }
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "concierge.bounty_deadline_gate",
                    "compile",
                    str(snapshot_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("SPONSOR_DEADLINE_ELAPSED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
