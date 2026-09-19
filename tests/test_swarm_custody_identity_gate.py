# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "swarm_custody_identity_gate.py"
SPEC = importlib.util.spec_from_file_location("swarm_custody_identity_gate", TOOL)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def custody(events, active_by_work):
    items = []
    for work_key in sorted(active_by_work):
        active = []
        for take_id, owner in active_by_work[work_key]:
            active.append({
                "owner": owner,
                "take_event_id": take_id,
                "taken_at": "2026-09-19T22:00:00.000000Z",
                "last_event_id": take_id,
                "last_at": "2026-09-19T22:00:00.000000Z",
                "age_seconds": 0.0,
                "stale": False,
            })
        items.append({
            "work_key": work_key,
            "disposition": "COLLISION" if len(active) > 1 else ("ACTIVE" if active else "OPEN"),
            "active_claims": active,
            "history": [e for e in events if e["work_key"] == work_key],
        })
    body = {
        "schema_version": 1,
        "as_of": "2026-09-19T22:00:00.000000Z",
        "lease_seconds": 3600,
        "event_count": len(events),
        "work_count": len(items),
        "counts": {
            "OPEN": 0,
            "ACTIVE": sum(len(v) == 1 for v in active_by_work.values()),
            "COLLISION": sum(len(v) > 1 for v in active_by_work.values()),
            "STALE": 0,
            "DONE": 0,
            "RELEASED": 0,
        },
        "events": events,
        "items": items,
    }
    return {**body, "receipt_sha256": hashlib.sha256(canonical(body)).hexdigest()}


def take(event_id, work_key, owner):
    return {
        "version": 1,
        "event_id": event_id,
        "work_key": work_key,
        "kind": "TAKE",
        "owner": owner,
        "at": "2026-09-19T22:00:00.000000Z",
    }


def binding(take_id, session, claim, issue):
    return {
        "take_event_id": take_id,
        "session_id": session,
        "claim_id": claim,
        "canonical_issue": issue,
    }


class IdentityGateTests(unittest.TestCase):
    def compile(self, events, active, bindings):
        return gate.compile_identity_gate({
            "schema": gate.SCHEMA,
            "custody_report": custody(events, active),
            "bindings": bindings,
        })

    def test_clear_for_distinct_sessions_claim_ids_and_issues(self):
        events = [take("e1", "GFOX2-001/R", "sol-a"), take("e2", "GFOX2-002/R", "sol-b")]
        receipt = self.compile(
            events,
            {"GFOX2-001/R": [("e1", "sol-a")], "GFOX2-002/R": [("e2", "sol-b")]},
            [
                binding("e1", "sess-a", "GFOX2-001", "Org/Repo#1"),
                binding("e2", "sess-b", "GFOX2-002", "Other/Repo#2"),
            ],
        )
        self.assertEqual(receipt["disposition"], "CLEAR")
        self.assertEqual(receipt["reason_codes"], [])
        self.assertTrue(gate.verify_identity_gate(receipt))
        self.assertEqual(receipt["bindings"][0]["canonical_issue"], "org/repo#1")

    def test_same_owner_label_two_active_sessions_holds(self):
        events = [take("e1", "a", "ZZ-Sol-47"), take("e2", "b", "ZZ-Sol-47")]
        receipt = self.compile(
            events,
            {"a": [("e1", "ZZ-Sol-47")], "b": [("e2", "ZZ-Sol-47")]},
            [
                binding("e1", "sess-a", "GFOX-1", "Org/Repo#1"),
                binding("e2", "sess-b", "GFOX-2", "Org/Repo#2"),
            ],
        )
        self.assertEqual(receipt["disposition"], "HOLD_COLLISION")
        self.assertIn("OWNER_LABEL_SESSION_COLLISION", receipt["reason_codes"])
        self.assertEqual(receipt["collisions"]["owner_labels"][0]["session_ids"], ["sess-a", "sess-b"])

    def test_same_session_two_active_owner_labels_holds(self):
        events = [take("e1", "a", "old-name"), take("e2", "b", "new-name")]
        receipt = self.compile(
            events,
            {"a": [("e1", "old-name")], "b": [("e2", "new-name")]},
            [
                binding("e1", "sess-a", "GFOX-1", "Org/Repo#1"),
                binding("e2", "sess-a", "GFOX-2", "Org/Repo#2"),
            ],
        )
        self.assertIn("SESSION_OWNER_LABEL_COLLISION", receipt["reason_codes"])
        self.assertEqual(receipt["collisions"]["session_ids"][0]["owners"], ["new-name", "old-name"])

    def test_claim_id_reused_for_different_issues_holds_even_if_one_not_active(self):
        events = [take("e1", "GFOX2-108/R", "alpha"), take("e2", "GFOX2-108/A", "beta")]
        report = custody(events, {"GFOX2-108/R": [], "GFOX2-108/A": [("e2", "beta")]})
        receipt = gate.compile_identity_gate({
            "schema": gate.SCHEMA,
            "custody_report": report,
            "bindings": [
                binding("e1", "sess-a", "GFOX2-20260919-108", "Org/First#10"),
                binding("e2", "sess-b", "GFOX2-20260919-108", "Org/Second#20"),
            ],
        })
        self.assertIn("CLAIM_ID_ISSUE_COLLISION", receipt["reason_codes"])
        row = receipt["collisions"]["claim_ids"][0]
        self.assertEqual(row["canonical_issues"], ["org/first#10", "org/second#20"])

    def test_same_claim_id_same_issue_allows_distinct_deliverables(self):
        events = [take("e1", "GFOX2-088/implementation", "alpha"), take("e2", "GFOX2-088/review", "beta")]
        receipt = self.compile(
            events,
            {"GFOX2-088/implementation": [("e1", "alpha")], "GFOX2-088/review": [("e2", "beta")]},
            [
                binding("e1", "sess-a", "GFOX2-088", "Stars/Forge#76"),
                binding("e2", "sess-b", "GFOX2-088", "stars/forge#76"),
            ],
        )
        self.assertEqual(receipt["disposition"], "CLEAR")

    def test_missing_extra_and_duplicate_bindings_fail_closed(self):
        events = [take("e1", "a", "alpha")]
        report = custody(events, {"a": [("e1", "alpha")]})
        base = {"schema": gate.SCHEMA, "custody_report": report}
        with self.assertRaisesRegex(gate.IdentityGateError, "missing bindings"):
            gate.compile_identity_gate({**base, "bindings": []})
        with self.assertRaisesRegex(gate.IdentityGateError, "unknown TAKE"):
            gate.compile_identity_gate({**base, "bindings": [binding("e9", "s", "c", "o/r#1")]})
        b = binding("e1", "s", "c", "o/r#1")
        with self.assertRaisesRegex(gate.IdentityGateError, "duplicate binding"):
            gate.compile_identity_gate({**base, "bindings": [b, copy.deepcopy(b)]})

    def test_tampered_custody_receipt_rejected(self):
        events = [take("e1", "a", "alpha")]
        report = custody(events, {"a": [("e1", "alpha")]})
        report["event_count"] = 99
        with self.assertRaisesRegex(gate.IdentityGateError, "receipt_sha256 does not match"):
            gate.compile_identity_gate({
                "schema": gate.SCHEMA,
                "custody_report": report,
                "bindings": [binding("e1", "s", "c", "o/r#1")],
            })

    def test_receipt_is_permutation_stable_and_tamper_detected(self):
        events = [take("e2", "b", "beta"), take("e1", "a", "alpha")]
        active = {"a": [("e1", "alpha")], "b": [("e2", "beta")]}
        bindings = [
            binding("e2", "s2", "C2", "O/R#2"),
            binding("e1", "s1", "C1", "O/R#1"),
        ]
        a = self.compile(events, active, bindings)
        b = self.compile(events, active, list(reversed(bindings)))
        self.assertEqual(a, b)
        tampered = copy.deepcopy(a)
        tampered["disposition"] = "HOLD_COLLISION"
        self.assertFalse(gate.verify_identity_gate(tampered))

    def test_cli_exit_zero_clear_three_collision_two_bad_input(self):
        clear_events = [take("e1", "a", "alpha")]
        clear_req = {
            "schema": gate.SCHEMA,
            "custody_report": custody(clear_events, {"a": [("e1", "alpha")]}),
            "bindings": [binding("e1", "s1", "C1", "O/R#1")],
        }
        collision_events = [take("e1", "a", "alpha"), take("e2", "b", "alpha")]
        collision_req = {
            "schema": gate.SCHEMA,
            "custody_report": custody(collision_events, {"a": [("e1", "alpha")], "b": [("e2", "alpha")]}),
            "bindings": [
                binding("e1", "s1", "C1", "O/R#1"),
                binding("e2", "s2", "C2", "O/R#2"),
            ],
        }
        self.assertEqual(self._run(clear_req).returncode, 0)
        collision = self._run(collision_req)
        self.assertEqual(collision.returncode, 3)
        self.assertEqual(json.loads(collision.stdout)["disposition"], "HOLD_COLLISION")
        bad = self._run({"schema": "wrong"})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stderr))

    def _run(self, request):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(json.dumps(request), encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-S", str(TOOL), str(path)],
                text=True,
                capture_output=True,
                check=False,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
