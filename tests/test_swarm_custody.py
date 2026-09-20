# SPDX-License-Identifier: MIT
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "swarm_custody.py"
SPEC = importlib.util.spec_from_file_location("swarm_custody", TOOL)
assert SPEC and SPEC.loader
custody = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = custody
SPEC.loader.exec_module(custody)


def ev(event_id, work_key, kind, owner, at, artifact=None):
    value = {
        "version": 1,
        "event_id": event_id,
        "work_key": work_key,
        "kind": kind,
        "owner": owner,
        "at": at,
    }
    if artifact is not None:
        value["artifact"] = artifact
    return custody.parse_event(value)


class SwarmCustodyTests(unittest.TestCase):
    AS_OF = custody.parse_time("2026-09-19T18:00:00-04:00", field="test")

    def report(self, events, lease=3600):
        return custody.build_report(events, as_of=self.AS_OF, lease_seconds=lease)

    def test_permutation_has_same_report_and_receipt(self):
        events = [
            ev("e3", "GFOX2-001/R", "PROGRESS", "sol-47", "2026-09-19T17:30:00-04:00"),
            ev("e1", "GFOX2-001/R", "TAKE", "sol-47", "2026-09-19T17:00:00-04:00"),
            ev("e4", "GFOX2-002/R", "DONE", "rook", "2026-09-19T17:40:00-04:00", "pr:2"),
            ev("e2", "GFOX2-002/R", "TAKE", "rook", "2026-09-19T17:10:00-04:00"),
        ]
        a = self.report(events)
        b = self.report(list(reversed(events)))
        self.assertEqual(a, b)
        self.assertEqual(a["counts"]["ACTIVE"], 1)
        self.assertEqual(a["counts"]["DONE"], 1)
        self.assertEqual(len(a["receipt_sha256"]), 64)

    def test_collision_wins_over_stale_and_cli_exits_three(self):
        events = [
            ev("e1", "GFOX2-010/A", "TAKE", "alpha", "2026-09-19T12:00:00-04:00"),
            ev("e2", "GFOX2-010/A", "TAKE", "beta", "2026-09-19T12:01:00-04:00"),
        ]
        report = self.report(events, lease=60)
        self.assertEqual(report["items"][0]["disposition"], "COLLISION")
        self.assertTrue(all(c["stale"] for c in report["items"][0]["active_claims"]))
        proc = self._run_cli(events, lease=60)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["counts"]["COLLISION"], 1)

    def test_stale_boundary_and_allow_stale(self):
        boundary_as_of = custody.parse_time("2026-09-19T18:00:00Z", field="test")
        claim = ev("e1", "work/1", "TAKE", "alpha", "2026-09-19T17:00:00Z")
        boundary = custody.build_report([claim], as_of=boundary_as_of, lease_seconds=3600)
        self.assertEqual(boundary["items"][0]["disposition"], "ACTIVE")

        stale_as_of = custody.parse_time("2026-09-19T18:00:01Z", field="test")
        stale = custody.build_report([claim], as_of=stale_as_of, lease_seconds=3600)
        self.assertEqual(stale["items"][0]["disposition"], "STALE")

        proc = self._run_cli([claim], as_of="2026-09-19T18:00:01Z", lease=3600)
        self.assertEqual(proc.returncode, 4)
        allowed = self._run_cli(
            [claim],
            as_of="2026-09-19T18:00:01Z",
            lease=3600,
            extra=["--allow-stale"],
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_done_and_release_transitions_are_distinct(self):
        done = self.report(
            [
                ev("e1", "done/key", "TAKE", "alpha", "2026-09-19T17:00:00-04:00"),
                ev("e2", "done/key", "DONE", "alpha", "2026-09-19T17:05:00-04:00", "pr:9"),
            ]
        )
        self.assertEqual(done["items"][0]["disposition"], "DONE")
        self.assertEqual(done["items"][0]["done"]["artifact"], "pr:9")

        released = self.report(
            [
                ev("e3", "released/key", "TAKE", "alpha", "2026-09-19T17:00:00-04:00"),
                ev("e4", "released/key", "RELEASE", "alpha", "2026-09-19T17:05:00-04:00"),
            ]
        )
        self.assertEqual(released["items"][0]["disposition"], "RELEASED")

        retaken = self.report(
            [
                ev("e5", "retake/key", "TAKE", "alpha", "2026-09-19T17:00:00-04:00"),
                ev("e6", "retake/key", "RELEASE", "alpha", "2026-09-19T17:05:00-04:00"),
                ev("e7", "retake/key", "TAKE", "beta", "2026-09-19T17:10:00-04:00"),
            ]
        )
        self.assertEqual(retaken["items"][0]["disposition"], "ACTIVE")
        self.assertEqual(retaken["items"][0]["active_claims"][0]["owner"], "beta")

    def test_duplicate_event_id_rejected(self):
        with self.assertRaisesRegex(custody.LedgerError, "duplicate event_id"):
            self.report(
                [
                    ev("same", "a", "TAKE", "alpha", "2026-09-19T17:00:00-04:00"),
                    ev("same", "b", "TAKE", "beta", "2026-09-19T17:01:00-04:00"),
                ]
            )

    def test_unknown_field_and_orphan_progress_rejected(self):
        raw = {
            "version": 1,
            "event_id": "e1",
            "work_key": "a",
            "kind": "TAKE",
            "owner": "alpha",
            "at": "2026-09-19T17:00:00Z",
            "surprise": True,
        }
        with self.assertRaisesRegex(custody.LedgerError, "unknown fields"):
            custody.parse_event(raw)
        with self.assertRaisesRegex(custody.LedgerError, "has no live TAKE"):
            self.report(
                [ev("e2", "a", "PROGRESS", "alpha", "2026-09-19T17:00:00-04:00")]
            )

    def test_done_is_terminal_and_collision_must_be_resolved(self):
        with self.assertRaisesRegex(custody.LedgerError, "appears after DONE"):
            self.report(
                [
                    ev("e1", "a", "TAKE", "alpha", "2026-09-19T17:00:00-04:00"),
                    ev("e2", "a", "DONE", "alpha", "2026-09-19T17:01:00-04:00"),
                    ev("e3", "a", "TAKE", "beta", "2026-09-19T17:02:00-04:00"),
                ]
            )
        with self.assertRaisesRegex(custody.LedgerError, "requires sole live custody"):
            self.report(
                [
                    ev("e4", "b", "TAKE", "alpha", "2026-09-19T17:00:00-04:00"),
                    ev("e5", "b", "TAKE", "beta", "2026-09-19T17:01:00-04:00"),
                    ev("e6", "b", "DONE", "alpha", "2026-09-19T17:02:00-04:00"),
                ]
            )

    def test_duplicate_json_keys_fail_closed_in_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.ndjson"
            path.write_text(
                '{"version":1,"event_id":"e1","event_id":"shadow","work_key":"a","kind":"TAKE","owner":"alpha","at":"2026-09-19T17:00:00Z"}\\n',
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(TOOL),
                    str(path),
                    "--as-of",
                    "2026-09-19T18:00:00Z",
                    "--lease-seconds",
                    "3600",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stderr)
        self.assertEqual(payload["error"]["code"], "duplicate_json_key")
        self.assertIn("line 1", payload["error"]["message"])

    def test_nonfinite_json_constants_fail_closed_in_cli(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "events.ndjson"
                    path.write_text(
                        '{"version":1,"event_id":"e1","work_key":"a","kind":"TAKE","owner":"alpha","at":"2026-09-19T17:00:00Z","artifact":'
                        + constant
                        + "}\\n",
                        encoding="utf-8",
                    )
                    proc = subprocess.run(
                        [
                            sys.executable,
                            "-S",
                            str(TOOL),
                            str(path),
                            "--as-of",
                            "2026-09-19T18:00:00Z",
                            "--lease-seconds",
                            "3600",
                        ],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                self.assertEqual(proc.returncode, 2, (constant, proc.stderr))
                payload = json.loads(proc.stderr)
                self.assertEqual(payload["error"]["code"], "invalid_json_constant")
                self.assertIn("line 1", payload["error"]["message"])

    def test_invalid_input_is_machine_readable_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.ndjson"
            path.write_text('{"not":"enough"}\n', encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(TOOL),
                    str(path),
                    "--as-of",
                    "2026-09-19T18:00:00Z",
                    "--lease-seconds",
                    "3600",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stderr)
        self.assertIn(payload["error"]["code"], {"missing_fields", "unknown_fields"})

    def _run_cli(self, events, *, as_of="2026-09-19T18:00:00-04:00", lease=3600, extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.ndjson"
            path.write_text(
                "".join(json.dumps(event.normalized(), sort_keys=True) + "\n" for event in events),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(TOOL),
                    str(path),
                    "--as-of",
                    as_of,
                    "--lease-seconds",
                    str(lease),
                    *(extra or []),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        return proc


if __name__ == "__main__":
    unittest.main()
