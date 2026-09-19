from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.grantfox_queue_gate import compile_grantfox_queue_gate
from concierge.grantfox_source_readiness import (
    GrantFoxSourceReadinessInputError,
    compile_grantfox_source_readiness,
    verify_source_readiness_receipt,
)


LIB_BLOB = "a46399fe9cac8238c11a61d9e47f5c10afb44633"
STORAGE_BLOB = "bdcc6ae0f9f797550cab3660d1985ac13fe8a0e5"


def queue_snapshot(**overrides):
    payload = {
        "schema": "grantfox-queue-gate/v1",
        "listing_url": (
            "https://contribute.grantfox.xyz/org/StellarStream-HQ/"
            "repo/StellarStream/issue/1448"
        ),
        "canonical_issue_url": (
            "https://github.com/StellarStream-HQ/StellarStream/issues/1448"
        ),
        "issue_state": "open",
        "actor_login": "woahwhattheheck",
        "assigned_to": None,
        "actor_applied": False,
        "application_count": 0,
        "application_pressure_threshold": 3,
        "linked_pr_urls": [],
        "labels": ["Maybe Rewarded", "GrantFox OSS", "Third Campaign"],
        "observed_at": "2026-09-19T21:40:00Z",
        "evaluated_at": "2026-09-19T21:41:00Z",
        "max_snapshot_age_seconds": 900,
    }
    payload.update(overrides)
    return payload


def queue_receipt(**overrides):
    return compile_grantfox_queue_gate(queue_snapshot(**overrides))


def match(path, blob_sha=LIB_BLOB):
    return {"path": path, "blob_sha": blob_sha}


def request(**overrides):
    payload = {
        "schema": "grantfox-source-readiness/v1",
        "queue_receipt": queue_receipt(),
        "repository_snapshot": {
            "repository_full_name": "StellarStream-HQ/StellarStream",
            "default_branch": "contributing",
            "commit_sha": "5003c2d22a8dfde3d6a1fe3498300e7f285acf89",
            "observed_at": "2026-09-19T21:41:30Z",
        },
        "expectations": [
            {
                "kind": "path",
                "value": "contracts/Contract-V1/src/lib.rs",
                "matches": [match("contracts/Contract-V1/src/lib.rs")],
            },
            {
                "kind": "symbol",
                "value": "get_user_streams",
                "matches": [match("contracts/Contract-V1/src/lib.rs")],
            },
        ],
        "replacements": [],
        "evaluated_at": "2026-09-19T21:42:00Z",
        "max_snapshot_age_seconds": 900,
    }
    payload.update(overrides)
    return payload


def stellarstream_drift_request():
    expectations = [
        {
            "kind": "path",
            "value": "contracts/Contract-V1/src/types.rs",
            "matches": [],
        },
        {"kind": "symbol", "value": "UserProfile", "matches": []},
        {"kind": "symbol", "value": "outgoing_streams", "matches": []},
        {"kind": "symbol", "value": "incoming_streams", "matches": []},
        {
            "kind": "symbol",
            "value": "DataKey::UserStreams",
            "matches": [
                match(
                    "contracts/Contract-V1/src/storage.rs",
                    STORAGE_BLOB,
                )
            ],
        },
        {
            "kind": "symbol",
            "value": "get_user_streams",
            "matches": [match("contracts/Contract-V1/src/lib.rs")],
        },
        {
            "kind": "symbol",
            "value": "add_user_stream",
            "matches": [match("contracts/Contract-V1/src/lib.rs")],
        },
    ]
    replacements = [
        {
            "for_kind": "path",
            "for_value": "contracts/Contract-V1/src/types.rs",
            "replacement_kind": "path",
            "replacement_value": "contracts/Contract-V1/src/lib.rs",
            "evidence": [match("contracts/Contract-V1/src/lib.rs")],
        },
        {
            "for_kind": "symbol",
            "for_value": "UserProfile",
            "replacement_kind": "symbol",
            "replacement_value": "DataKey::UserStreams",
            "evidence": [
                match(
                    "contracts/Contract-V1/src/storage.rs",
                    STORAGE_BLOB,
                )
            ],
        },
        {
            "for_kind": "symbol",
            "for_value": "outgoing_streams",
            "replacement_kind": "symbol",
            "replacement_value": "get_user_streams",
            "evidence": [match("contracts/Contract-V1/src/lib.rs")],
        },
        {
            "for_kind": "symbol",
            "for_value": "incoming_streams",
            "replacement_kind": "symbol",
            "replacement_value": "get_user_streams",
            "evidence": [match("contracts/Contract-V1/src/lib.rs")],
        },
    ]
    return request(expectations=expectations, replacements=replacements)


class GrantFoxSourceReadinessTests(unittest.TestCase):
    def test_aligned_source_is_pinned_and_advisory_only(self):
        receipt = compile_grantfox_source_readiness(request())
        self.assertEqual(receipt["source_disposition"], "SOURCE_ALIGNED")
        self.assertEqual(receipt["queue_disposition"], "APPLY_ELIGIBLE")
        self.assertEqual(receipt["reason_codes"], [])
        self.assertFalse(receipt["authority"]["provider_application_authority"])
        self.assertFalse(receipt["authority"]["implementation_write_authority"])
        self.assertTrue(verify_source_readiness_receipt(receipt))

    def test_stellarstream_stale_issue_contract_becomes_replan_not_false_green(self):
        receipt = compile_grantfox_source_readiness(stellarstream_drift_request())
        self.assertEqual(receipt["source_disposition"], "SOURCE_DRIFT_REPLAN")
        self.assertEqual(receipt["drift"]["missing_count"], 4)
        self.assertEqual(receipt["drift"]["resolved_replacement_count"], 4)
        self.assertEqual(receipt["drift"]["unresolved_count"], 0)
        self.assertIn(
            "ISSUE_SOURCE_REFERENCES_MISSING",
            receipt["reason_codes"],
        )
        self.assertTrue(verify_source_readiness_receipt(receipt))

    def test_missing_reference_without_replacement_holds(self):
        payload = request(
            expectations=[
                {"kind": "symbol", "value": "UserProfile", "matches": []}
            ]
        )
        receipt = compile_grantfox_source_readiness(payload)
        self.assertEqual(receipt["source_disposition"], "HOLD")
        self.assertIn("UNRESOLVED_SOURCE_DRIFT", receipt["reason_codes"])

    def test_stale_source_snapshot_holds_even_when_queue_is_fresh(self):
        payload = request(
            queue_receipt=queue_receipt(
                observed_at="2026-09-19T21:59:00Z",
                evaluated_at="2026-09-19T21:59:10Z",
            ),
            repository_snapshot={
                "repository_full_name": "StellarStream-HQ/StellarStream",
                "default_branch": "contributing",
                "commit_sha": "5003c2d22a8dfde3d6a1fe3498300e7f285acf89",
                "observed_at": "2026-09-19T21:00:00Z",
            },
            evaluated_at="2026-09-19T22:00:01Z",
        )
        receipt = compile_grantfox_source_readiness(payload)
        self.assertEqual(receipt["source_disposition"], "HOLD")
        self.assertIn("SOURCE_SNAPSHOT_STALE", receipt["reason_codes"])
        self.assertNotIn("QUEUE_RECEIPT_STALE", receipt["reason_codes"])

    def test_queue_receipt_that_aged_out_before_source_evaluation_holds(self):
        payload = request(
            repository_snapshot={
                "repository_full_name": "StellarStream-HQ/StellarStream",
                "default_branch": "contributing",
                "commit_sha": "5003c2d22a8dfde3d6a1fe3498300e7f285acf89",
                "observed_at": "2026-09-19T21:55:30Z",
            },
            evaluated_at="2026-09-19T21:56:00Z",
        )
        receipt = compile_grantfox_source_readiness(payload)
        self.assertEqual(receipt["source_disposition"], "HOLD")
        self.assertIn("QUEUE_RECEIPT_STALE", receipt["reason_codes"])

    def test_repository_identity_must_match_verified_queue_issue(self):
        payload = request()
        payload["repository_snapshot"]["repository_full_name"] = "Other/Repo"
        with self.assertRaisesRegex(
            GrantFoxSourceReadinessInputError,
            "identity does not match",
        ):
            compile_grantfox_source_readiness(payload)

    def test_path_traversal_and_noncanonical_paths_are_rejected(self):
        hostile = ("../secret.rs", "/rooted.rs", "a//b.rs", "a\\b.rs", "a/./b.rs")
        for path in hostile:
            with self.subTest(path=path):
                payload = request(
                    expectations=[{"kind": "path", "value": path, "matches": []}]
                )
                with self.assertRaises(GrantFoxSourceReadinessInputError):
                    compile_grantfox_source_readiness(payload)

    def test_duplicate_expectations_are_rejected(self):
        expected = {"kind": "symbol", "value": "UserProfile", "matches": []}
        with self.assertRaisesRegex(
            GrantFoxSourceReadinessInputError,
            "duplicate expectation",
        ):
            compile_grantfox_source_readiness(
                request(expectations=[expected, deepcopy(expected)])
            )

    def test_path_expectation_cannot_be_satisfied_by_different_path(self):
        with self.assertRaisesRegex(
            GrantFoxSourceReadinessInputError,
            "path match must equal",
        ):
            compile_grantfox_source_readiness(
                request(
                    expectations=[
                        {
                            "kind": "path",
                            "value": "src/a.rs",
                            "matches": [match("src/b.rs")],
                        }
                    ]
                )
            )

    def test_replacement_must_target_a_missing_declared_expectation(self):
        payload = request(
            replacements=[
                {
                    "for_kind": "symbol",
                    "for_value": "get_user_streams",
                    "replacement_kind": "symbol",
                    "replacement_value": "anything",
                    "evidence": [match("contracts/Contract-V1/src/lib.rs")],
                }
            ]
        )
        with self.assertRaisesRegex(
            GrantFoxSourceReadinessInputError,
            "only replace a missing expectation",
        ):
            compile_grantfox_source_readiness(payload)

    def test_replacement_cannot_invent_an_undeclared_stale_reference(self):
        payload = request(
            replacements=[
                {
                    "for_kind": "symbol",
                    "for_value": "not-in-expectations",
                    "replacement_kind": "symbol",
                    "replacement_value": "anything",
                    "evidence": [match("contracts/Contract-V1/src/lib.rs")],
                }
            ]
        )
        with self.assertRaisesRegex(
            GrantFoxSourceReadinessInputError,
            "existing expectation",
        ):
            compile_grantfox_source_readiness(payload)

    def test_tamper_of_drift_summary_breaks_verification(self):
        receipt = compile_grantfox_source_readiness(stellarstream_drift_request())
        changed = deepcopy(receipt)
        changed["drift"]["missing_count"] = 99
        self.assertFalse(verify_source_readiness_receipt(changed))

    def test_tamper_of_embedded_queue_receipt_breaks_verification(self):
        receipt = compile_grantfox_source_readiness(request())
        changed = deepcopy(receipt)
        changed["queue_receipt"]["provider_snapshot"]["application_count"] = 99
        self.assertFalse(verify_source_readiness_receipt(changed))

    def test_wait_assignment_never_becomes_implementation_authority(self):
        receipt = compile_grantfox_source_readiness(
            request(queue_receipt=queue_receipt(actor_applied=True))
        )
        self.assertEqual(receipt["queue_disposition"], "WAIT_ASSIGNMENT")
        self.assertEqual(receipt["source_disposition"], "SOURCE_ALIGNED")
        self.assertFalse(receipt["authority"]["implementation_write_authority"])

    def test_cli_round_trip_and_hold_exit_code(self):
        payload = request(
            expectations=[
                {"kind": "symbol", "value": "UserProfile", "matches": []}
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "concierge.grantfox_source_readiness",
                    str(path),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        receipt = json.loads(proc.stdout)
        self.assertEqual(receipt["source_disposition"], "HOLD")
        self.assertTrue(verify_source_readiness_receipt(receipt))


if __name__ == "__main__":
    unittest.main()
