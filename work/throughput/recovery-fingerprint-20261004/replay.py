# SPDX-License-Identifier: MIT
"""Compare actual recovery modules using synthetic captures and no provider I/O.

Usage: python replay.py --baseline /path/to/old_recovery.py --output results.json
Run with the repository on PYTHONPATH. Retained captures contain synthetic prose,
not current bounty, payment, claim or provider observations.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import socket
import statistics
import tempfile
import time
from unittest.mock import patch

import requests
from concierge import bounty_capture_recover as candidate
from concierge.bounty_capture import capture_digest, replay_capture, _issue_projection
from concierge.bounty_preflight import _apply_assignee_gate, _apply_maintainer_contribution_pause_gate
from concierge.bounty_qualification import qualify_dispatch
from concierge.credential_safety import apply_credential_gate, credential_gate_signal_types


def fixture(number: int, body_size: int) -> dict:
    """Produce a valid closed-issue replay fixture, never an eligible live claim."""
    repo = "synthetic/recovery-fixture"
    assignment = {"formal_assignee_count": 0, "assigned_to_operator": False,
                  "foreign_assignee_count": 0}
    base = {"title": "Synthetic recovery fixture", "body": "Offline prose.\n" * (body_size // 15),
            "labels": [], "attempt_count": 0,
            "canonical_audit": {"issue_state": "closed", "open_pr_count": 0,
                                "stale_listing_signal": False, "search_truncated": False}}
    q = qualify_dispatch({"repo": repo, "submission_target": None, **base}, saturation_threshold=4)
    q = apply_credential_gate(q, credential_gate_signal_types([base["body"]]))
    q = _apply_maintainer_contribution_pause_gate(q, 0)
    q = _apply_assignee_gate(q, assignment)
    assert not q["dispatch"]
    issue = f"https://api.github.com/repos/{repo}/issues/{number}"
    value = {
        "schema": "bounty-preflight-capture/v1", "repo": repo, "number": number,
        "issue_url": f"https://github.com/{repo}/issues/{number}",
        "policy": {"max_pages": 1, "saturation_threshold": 4},
        "observation": {"started_at": "2026-10-01T00:00:00Z", "completed_at": "2026-10-01T00:00:01Z",
                        "request_count": 3, "source_urls": sorted([issue, issue + "/comments",
                                                                   "https://api.github.com/search/issues"])},
        "baseline": base, "authority": {"issue_author_association": "OWNER", "maintainer_comments": []},
        "assignment": {**assignment, "principal_check": "NO_ASSIGNEES"},
        "generation": {"issue_sha256": "0" * 64, "issue_projection_sha256": "0" * 64,
                       "issue_updated_at": "2026-09-30T00:00:00Z", "issue_comment_count": 0,
                       "comments": [], "comments_truncated": False, "attempt_signal_count": 0},
        "checks": {"audit_before": None, "generation": None, "audit_after": None}, "qualification": q,
    }
    value["generation"]["issue_projection_sha256"] = capture_digest(_issue_projection(value))
    value["receipt_sha256"] = capture_digest(value)
    replay_capture(value, saturation_threshold=4)
    return value


def wire(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def git_blob(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("recovery_baseline", args.baseline)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    network_attempts = []

    def no_network(*args, **kwargs):
        network_attempts.append(True)
        raise AssertionError("offline recovery attempted provider I/O")

    def compare(root, name, payloads, rows, expected, repeats=1, capacity=128):
        source = root / name
        source.mkdir(mode=0o700)
        (source / "shortlist.json").write_text(json.dumps({"candidates": rows}))
        for index, payload in enumerate(payloads):
            (source / f"capture-{index:03}.json").write_bytes(payload)
        original_files = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
        samples = {"baseline": [], "candidate": []}
        counts = {}
        for repeat in range(repeats):
            outputs = {}
            order = [("baseline", baseline), ("candidate", candidate)]
            if repeat % 2:
                order.reverse()
            for label, module in order:
                output = root / f"{name}-{label}-{repeat}"
                with patch.object(module, "replay_capture", wraps=module.replay_capture) as replay, \
                     patch.object(module, "_REPLAY_CACHE_ENTRIES", capacity):
                    started = time.perf_counter()
                    result = module.recover_batch(source, output)
                    samples[label].append(time.perf_counter() - started)
                    counts[label] = replay.call_count
                assert result["request_count"] == 0
                assert output.stat().st_mode & 0o777 == 0o700
                files = {p.name: p.read_bytes() for p in output.iterdir() if p.name != "summary.json"}
                assert all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir())
                assert json.loads((output / "summary.json").read_bytes()) == result
                assert all(files[f"capture-{i:03}.json"] == raw for i, raw in enumerate(payloads))
                outputs[label] = (files, {k: v for k, v in result.items() if k not in {
                    "elapsed_seconds", "recovery_started_at", "recovery_completed_at"}})
            assert outputs["baseline"] == outputs["candidate"], name
            assert (counts["baseline"], counts["candidate"]) == expected, (name, counts)
        assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()} == original_files
        print(f"{name}: replay calls {counts}; outputs identical", flush=True)
        return {"case": name, "repeats": repeats, "file_sizes": [len(p) for p in payloads],
                "replay_calls": counts, "median_ms": {k: round(statistics.median(v)*1000, 3)
                                                      for k, v in samples.items()}, "equivalent": True}

    with patch.object(requests.sessions.Session, "request", no_network), \
         patch.object(socket.socket, "connect", no_network), \
         tempfile.TemporaryDirectory(prefix="recovery-fingerprint-") as temp:
        root = Path(temp)
        small = fixture(1, 4000)
        large = fixture(1, 1200000)
        medium = fixture(1, 600000)
        sw, lw, mw = wire(small), wire(large), wire(medium)
        assert len(lw) > 1024 * 1024 and 2 * len(mw) > 1024 * 1024
        rows = [{"repo": small["repo"], "number": 1}]
        changed = deepcopy(small)
        changed["baseline"]["body"] += " changed bytes"
        conflict = deepcopy(small)
        conflict["observation"]["completed_at"] = "2026-10-01T00:00:02Z"
        del conflict["receipt_sha256"]
        conflict["receipt_sha256"] = capture_digest(conflict)
        cases = [
            compare(root, "small-duplicates", [sw] * 4, rows, (1, 1), repeats=3),
            compare(root, "large-duplicates", [lw] * 4, rows, (4, 1), repeats=3),
            compare(root, "alternating-medium", [mw, b" " + mw] * 2, rows, (4, 2), repeats=3),
            compare(root, "changed-and-malformed", [sw, wire(changed), b"{", sw], rows, (2, 2)),
            compare(root, "conflicting-observations", [sw, wire(conflict), sw], rows, (2, 2)),
            compare(root, "entry-limit-eviction", [sw, b" " + sw, b"  " + sw, sw], rows, (4, 4), capacity=2),
            compare(root, "next-invocation", [lw, lw], rows, (2, 1)),
        ]
    assert not network_attempts
    report = {"baseline_blob": git_blob(args.baseline.read_bytes()),
              "candidate_blob": git_blob(Path(candidate.__file__).read_bytes()),
              "python": platform.python_version(), "requests": requests.__version__,
              "fixture_scope": "Synthetic closed-issue captures; offline in-process recovery only.",
              "provider_requests": 0, "cases_passed": len(cases), "measurements": cases}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
