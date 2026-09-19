from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.publication_route_gate import (
    PublicationRouteInputError,
    compile_publication_route,
    verify_receipt,
)


PRIMITIVES = [
    "create_branch",
    "create_file",
    "update_file",
    "create_blob",
    "create_tree",
    "create_commit",
    "update_ref",
    "create_pull_request",
    "merge_pull_request",
]


def observation(**overrides):
    payload = {
        "schema": "publication-route-gate/v1",
        "actor_login": "woahwhattheheck",
        "upstream_repo": "GrantChain/GrantFox",
        "upstream_default_branch": "develop",
        "target_base_branch": "develop",
        "integration_access": "resource_not_accessible",
        "installed_fork_repo": None,
        "installed_fork_push_access": None,
        "publication_primitives": PRIMITIVES,
        "existing_pr_url": None,
        "existing_pr_state": None,
        "provider_requires_assignment": False,
        "provider_assignment": "none",
        "actor_applied": False,
        "observed_at": "2026-09-19T21:49:00Z",
        "evaluated_at": "2026-09-19T21:50:00Z",
        "max_snapshot_age_seconds": 900,
    }
    payload.update(overrides)
    return payload


class PublicationRouteGateTests(unittest.TestCase):
    def test_external_connector_403_is_unknown_not_user_permission_denial(self):
        receipt = compile_publication_route(observation())
        self.assertEqual(receipt["disposition"], "HANDOFF_REQUIRED")
        self.assertIn(
            "UPSTREAM_CONNECTOR_ACCESS_UNKNOWN_NOT_PERMISSION_DENIAL",
            receipt["reason_codes"],
        )
        self.assertEqual(
            receipt["interpretation"]["resource_not_accessible_means"],
            "CONNECTOR_ACCESS_UNKNOWN",
        )
        self.assertEqual(
            receipt["interpretation"]["resource_not_accessible_does_not_prove"],
            "USER_LACKS_REPOSITORY_PERMISSION",
        )

    def test_observed_upstream_write_routes_directly(self):
        receipt = compile_publication_route(observation(integration_access="write"))
        self.assertEqual(receipt["disposition"], "DIRECT_BRANCH_PR")
        self.assertEqual(receipt["advisory_next_action"], "PUBLISH_BRANCH_AND_PR_TO_BOUND_BASE")

    def test_external_unknown_plus_owned_installed_fork_routes_via_fork(self):
        receipt = compile_publication_route(
            observation(
                installed_fork_repo="woahwhattheheck/GrantFox",
                installed_fork_push_access=True,
            )
        )
        self.assertEqual(receipt["disposition"], "OWNED_FORK_PR")
        self.assertIn("INSTALLED_FORK_PUSH_OBSERVED", receipt["reason_codes"])
        self.assertIn(
            "UPSTREAM_CONNECTOR_ACCESS_UNKNOWN_NOT_PERMISSION_DENIAL",
            receipt["reason_codes"],
        )

    def test_read_only_upstream_plus_fork_routes_via_fork(self):
        receipt = compile_publication_route(
            observation(
                integration_access="read",
                installed_fork_repo="woahwhattheheck/GrantFox",
                installed_fork_push_access=True,
            )
        )
        self.assertEqual(receipt["disposition"], "OWNED_FORK_PR")

    def test_writable_foreign_fork_is_not_treated_as_actor_owned(self):
        receipt = compile_publication_route(
            observation(
                installed_fork_repo="someone-else/GrantFox",
                installed_fork_push_access=True,
            )
        )
        self.assertEqual(receipt["disposition"], "HANDOFF_REQUIRED")
        self.assertIn("INSTALLED_FORK_NOT_ACTOR_OWNED", receipt["reason_codes"])
        self.assertFalse(receipt["observed_publication_path"]["installed_fork_actor_owned"])

    def test_missing_publication_primitives_requires_handoff(self):
        receipt = compile_publication_route(
            observation(integration_access="write", publication_primitives=["create_file"])
        )
        self.assertEqual(receipt["disposition"], "HANDOFF_REQUIRED")
        self.assertIn("PUBLICATION_PRIMITIVES_INSUFFICIENT", receipt["reason_codes"])

    def test_assignment_required_before_application_yields_application_only(self):
        receipt = compile_publication_route(
            observation(provider_requires_assignment=True, provider_assignment="none")
        )
        self.assertEqual(receipt["disposition"], "APPLICATION_ONLY")
        self.assertFalse(receipt["authority"]["provider_application_authority"])

    def test_existing_application_waits_for_assignment(self):
        receipt = compile_publication_route(
            observation(
                provider_requires_assignment=True,
                provider_assignment="none",
                actor_applied=True,
            )
        )
        self.assertEqual(receipt["disposition"], "WAIT_ASSIGNMENT")

    def test_assignment_to_actor_unlocks_observed_fork_route(self):
        receipt = compile_publication_route(
            observation(
                provider_requires_assignment=True,
                provider_assignment="actor",
                actor_applied=True,
                installed_fork_repo="woahwhattheheck/GrantFox",
                installed_fork_push_access=True,
            )
        )
        self.assertEqual(receipt["disposition"], "OWNED_FORK_PR")

    def test_assignment_to_other_holds_even_with_write_access(self):
        receipt = compile_publication_route(
            observation(integration_access="write", provider_assignment="other")
        )
        self.assertEqual(receipt["disposition"], "HOLD_ASSIGNED_TO_OTHER")

    def test_unknown_assignment_fails_closed(self):
        receipt = compile_publication_route(observation(provider_assignment="unknown"))
        self.assertEqual(receipt["disposition"], "HOLD_ASSIGNMENT_UNKNOWN")

    def test_existing_upstream_pr_is_reused_before_new_branch_routing(self):
        receipt = compile_publication_route(
            observation(
                integration_access="write",
                existing_pr_url="https://github.com/GrantChain/GrantFox/pull/99",
                existing_pr_state="open",
            )
        )
        self.assertEqual(receipt["disposition"], "REUSE_EXISTING_PR")
        self.assertEqual(receipt["target"]["existing_pr_url"], "https://github.com/GrantChain/GrantFox/pull/99")
        self.assertEqual(receipt["target"]["existing_pr_state"], "open")

    def test_unknown_existing_pr_state_holds_instead_of_reusing(self):
        receipt = compile_publication_route(
            observation(
                integration_access="write",
                existing_pr_url="https://github.com/GrantChain/GrantFox/pull/99",
                existing_pr_state="unknown",
            )
        )
        self.assertEqual(receipt["disposition"], "HOLD_EXISTING_PR_STATE_UNKNOWN")

    def test_closed_or_merged_existing_pr_holds_before_new_work(self):
        for state in ("closed", "merged"):
            with self.subTest(state=state):
                receipt = compile_publication_route(
                    observation(
                        integration_access="write",
                        existing_pr_url="https://github.com/GrantChain/GrantFox/pull/99",
                        existing_pr_state=state,
                    )
                )
                self.assertEqual(receipt["disposition"], "HOLD_EXISTING_PR_NOT_OPEN")

    def test_existing_pr_url_and_state_must_move_together(self):
        with self.assertRaisesRegex(PublicationRouteInputError, "must be null"):
            compile_publication_route(observation(existing_pr_state="open"))

    def test_existing_pr_must_belong_to_upstream(self):
        with self.assertRaisesRegex(PublicationRouteInputError, "belong to upstream_repo"):
            compile_publication_route(
                observation(
                    existing_pr_url="https://github.com/other/repo/pull/99",
                    existing_pr_state="open",
                )
            )

    def test_nondefault_required_base_is_preserved_not_rewritten(self):
        receipt = compile_publication_route(
            observation(
                integration_access="write",
                upstream_default_branch="main",
                target_base_branch="develop",
            )
        )
        self.assertEqual(receipt["disposition"], "DIRECT_BRANCH_PR")
        self.assertEqual(receipt["target"]["target_base_branch"], "develop")
        self.assertFalse(receipt["target"]["target_is_default_branch"])

    def test_stale_observation_holds_before_any_route(self):
        receipt = compile_publication_route(
            observation(
                integration_access="write",
                observed_at="2026-09-19T20:00:00Z",
                evaluated_at="2026-09-19T21:00:01Z",
                max_snapshot_age_seconds=3600,
            )
        )
        self.assertEqual(receipt["disposition"], "HOLD_STALE_OBSERVATION")

    def test_fork_push_without_fork_is_contradictory(self):
        with self.assertRaisesRegex(PublicationRouteInputError, "must be null"):
            compile_publication_route(observation(installed_fork_push_access=True))

    def test_duplicate_or_unknown_primitives_fail_closed(self):
        for primitives in (
            ["create_branch", "create_branch"],
            ["create_branch", "fork_repository"],
        ):
            with self.subTest(primitives=primitives):
                with self.assertRaises(PublicationRouteInputError):
                    compile_publication_route(observation(publication_primitives=primitives))

    def test_receipt_is_deterministic_and_tamper_evident(self):
        first = compile_publication_route(observation())
        second = compile_publication_route(observation())
        self.assertEqual(first, second)
        self.assertTrue(verify_receipt(first))
        changed = deepcopy(first)
        changed["observed_publication_path"]["integration_access"] = "write"
        self.assertFalse(verify_receipt(changed))
        escalated = deepcopy(first)
        escalated["authority"]["merge_authority"] = True
        self.assertFalse(verify_receipt(escalated))

    def test_cli_ready_and_handoff_exit_codes(self):
        cases = ((observation(integration_access="write"), 0), (observation(), 2))
        for payload, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "observation.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "concierge.publication_route_gate",
                        str(path),
                        "--json",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                    cwd=str(Path(__file__).resolve().parents[1]),
                )
                self.assertEqual(proc.returncode, expected, proc.stderr)
                self.assertTrue(verify_receipt(json.loads(proc.stdout)))


if __name__ == "__main__":
    unittest.main()
