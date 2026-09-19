from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.grantfox_dependency_readiness import (
    GrantFoxDependencyReadinessInputError,
    compile_grantfox_dependency_readiness,
    verify_dependency_readiness_receipt,
)
from concierge.grantfox_queue_gate import compile_grantfox_queue_gate
from concierge.grantfox_source_readiness import compile_grantfox_source_readiness


COMMIT = "5" * 40
BLOB = "a" * 40


def source_receipt(*, hold=False):
    queue = compile_grantfox_queue_gate(
        {
            "schema": "grantfox-queue-gate/v1",
            "listing_url": "https://contribute.grantfox.xyz/org/Gryd-lock/repo/grydlock-testkit/issue/33",
            "canonical_issue_url": "https://github.com/Gryd-lock/grydlock-testkit/issues/33",
            "issue_state": "open",
            "actor_login": "woahwhattheheck",
            "assigned_to": None,
            "actor_applied": False,
            "application_count": 1,
            "application_pressure_threshold": 3,
            "linked_pr_urls": [],
            "labels": ["GrantFox OSS", "Maybe Rewarded"],
            "observed_at": "2026-09-19T22:00:00Z",
            "evaluated_at": "2026-09-19T22:00:10Z",
            "max_snapshot_age_seconds": 900,
        }
    )
    expectations = [
        {
            "kind": "path",
            "value": "transactions/README.md",
            "matches": [
                {"path": "transactions/README.md", "blob_sha": BLOB}
            ],
        }
    ]
    if hold:
        expectations = [
            {"kind": "path", "value": "missing/tool.py", "matches": []}
        ]
    return compile_grantfox_source_readiness(
        {
            "schema": "grantfox-source-readiness/v1",
            "queue_receipt": queue,
            "repository_snapshot": {
                "repository_full_name": "Gryd-lock/grydlock-testkit",
                "default_branch": "main",
                "commit_sha": COMMIT,
                "observed_at": "2026-09-19T22:00:20Z",
            },
            "expectations": expectations,
            "replacements": [],
            "evaluated_at": "2026-09-19T22:00:30Z",
            "max_snapshot_age_seconds": 900,
        }
    )


def dependency(number=27, state="open", **overrides):
    item = {
        "repository_full_name": "Gryd-lock/grydlock-testkit",
        "issue_number": number,
        "issue_url": f"https://github.com/Gryd-lock/grydlock-testkit/issues/{number}",
        "state": state,
        "observed_at": "2026-09-19T22:01:00Z",
        "basis": "Issue #33 explicitly says to build on describe-transactions tooling.",
    }
    item.update(overrides)
    return item


def request(**overrides):
    payload = {
        "schema": "grantfox-dependency-readiness/v1",
        "source_receipt": source_receipt(),
        "dependencies": [dependency()],
        "evaluated_at": "2026-09-19T22:02:00Z",
        "max_snapshot_age_seconds": 900,
    }
    payload.update(overrides)
    return payload


class GrantFoxDependencyReadinessTests(unittest.TestCase):
    def test_open_prerequisite_waits_without_granting_authority(self):
        receipt = compile_grantfox_dependency_readiness(request())
        self.assertEqual(receipt["dependency_disposition"], "DEPENDENCY_WAIT")
        self.assertEqual(receipt["dependency_summary"]["open_issue_numbers"], [27])
        self.assertIn("PREREQUISITE_ISSUES_OPEN", receipt["reason_codes"])
        self.assertFalse(receipt["authority"]["implementation_write_authority"])
        self.assertFalse(receipt["authority"]["provider_application_authority"])
        self.assertTrue(verify_dependency_readiness_receipt(receipt))

    def test_all_closed_prerequisites_clear(self):
        receipt = compile_grantfox_dependency_readiness(
            request(
                dependencies=[
                    dependency(state="closed"),
                    dependency(4, "closed"),
                ]
            )
        )
        self.assertEqual(
            receipt["dependency_disposition"], "DEPENDENCIES_CLEAR"
        )
        self.assertEqual(receipt["reason_codes"], [])
        self.assertEqual(receipt["dependency_summary"]["closed_count"], 2)

    def test_source_hold_cannot_be_upgraded_by_closed_dependency(self):
        receipt = compile_grantfox_dependency_readiness(
            request(
                source_receipt=source_receipt(hold=True),
                dependencies=[dependency(state="closed")],
            )
        )
        self.assertEqual(receipt["dependency_disposition"], "HOLD")
        self.assertIn("SOURCE_NOT_ALIGNED", receipt["reason_codes"])

    def test_stale_dependency_snapshot_holds(self):
        receipt = compile_grantfox_dependency_readiness(
            request(
                dependencies=[
                    dependency(
                        observed_at="2026-09-19T21:00:00Z",
                        state="closed",
                    )
                ]
            )
        )
        self.assertEqual(receipt["dependency_disposition"], "HOLD")
        self.assertIn(
            "DEPENDENCY_SNAPSHOT_STALE", receipt["reason_codes"]
        )

    def test_cross_repo_dependency_is_rejected(self):
        with self.assertRaisesRegex(
            GrantFoxDependencyReadinessInputError,
            "must reference a prerequisite",
        ):
            compile_grantfox_dependency_readiness(
                request(
                    dependencies=[
                        dependency(
                            repository_full_name="Other/repo",
                            issue_url="https://github.com/Other/repo/issues/27",
                        )
                    ]
                )
            )

    def test_self_dependency_is_rejected(self):
        with self.assertRaisesRegex(
            GrantFoxDependencyReadinessInputError, "must not self-depend"
        ):
            compile_grantfox_dependency_readiness(
                request(dependencies=[dependency(33)])
            )

    def test_duplicate_dependency_is_rejected(self):
        with self.assertRaisesRegex(
            GrantFoxDependencyReadinessInputError, "duplicate dependency"
        ):
            compile_grantfox_dependency_readiness(
                request(dependencies=[dependency(), deepcopy(dependency())])
            )

    def test_noncanonical_issue_url_is_rejected(self):
        with self.assertRaisesRegex(
            GrantFoxDependencyReadinessInputError, "must equal canonical"
        ):
            compile_grantfox_dependency_readiness(
                request(
                    dependencies=[
                        dependency(
                            issue_url="https://example.com/issues/27"
                        )
                    ]
                )
            )

    def test_tamper_breaks_verification(self):
        receipt = compile_grantfox_dependency_readiness(request())
        changed = deepcopy(receipt)
        changed["dependency_summary"]["open_count"] = 0
        self.assertFalse(verify_dependency_readiness_receipt(changed))

    def test_tampered_source_receipt_is_rejected(self):
        bad = source_receipt()
        bad["identity"]["issue_number"] = 999
        with self.assertRaisesRegex(
            GrantFoxDependencyReadinessInputError, "source_receipt failed"
        ):
            compile_grantfox_dependency_readiness(
                request(source_receipt=bad)
            )

    def test_future_observation_is_rejected(self):
        with self.assertRaisesRegex(
            GrantFoxDependencyReadinessInputError, "must not be after"
        ):
            compile_grantfox_dependency_readiness(
                request(
                    dependencies=[
                        dependency(
                            observed_at="2026-09-19T23:00:00Z"
                        )
                    ]
                )
            )

    def test_cli_wait_exit_code_and_verified_receipt(self):
        payload = request()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dependency.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "concierge.grantfox_dependency_readiness",
                    str(path),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        receipt = json.loads(proc.stdout)
        self.assertEqual(
            receipt["dependency_disposition"], "DEPENDENCY_WAIT"
        )
        self.assertTrue(verify_dependency_readiness_receipt(receipt))


if __name__ == "__main__":
    unittest.main()
