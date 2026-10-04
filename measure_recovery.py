# SPDX-License-Identifier: MIT
"""Bounded offline replay comparison; fixtures below are synthetic, not provider observations."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
SOURCE = ROOT / "concierge/bounty_capture_recover.py"
BASELINE_BLOB = "d26bc68eefe9422629fb52f7f15af10eace580a9"
CANDIDATE_BLOB = "9cc60591fd13c4189fec6ae31a858a711203b594"

def blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()

base_raw = SOURCE.read_bytes()
assert blob(base_raw) == BASELINE_BLOB
text = base_raw.decode().replace("import argparse\n", "import argparse\nfrom collections import OrderedDict\n", 1)
text = text.replace("\n\ndef _read_source", "\n\n# Bound additional retained raw bytes; parsed captures are already in grouped.\n_REPLAY_CACHE_BYTES = 1024 * 1024\n_REPLAY_CACHE_ENTRIES = 128\n\n\ndef _read_source", 1)
text = text.replace("    items: list[dict[str, Any]] = []\n", '''    items: list[dict[str, Any]] = []
    # Key by exact file bytes, never the unverified receipt or issue identity.
    # Successful replay is deterministic within this recovery invocation.
    replayed: OrderedDict[bytes, tuple[dict[str, Any], dict[str, Any]]] = OrderedDict()
    replayed_bytes = 0
''', 1)
text = text.replace('''                capture = json.loads(raw)
                _, qualification, _ = replay_capture(
                    capture, saturation_threshold=capture["policy"]["saturation_threshold"],
                )''', '''                cached = replayed.get(raw)
                if cached is None:
                    capture = json.loads(raw)
                    _, qualification, _ = replay_capture(
                        capture, saturation_threshold=capture["policy"]["saturation_threshold"],
                    )
                else:
                    capture, qualification = cached
                    replayed.move_to_end(raw)''', 1)
text = text.replace('''                    grouped.setdefault(identity, []).append((capture, item))''', '''                    grouped.setdefault(identity, []).append((capture, item))
                    if cached is None and len(raw) <= _REPLAY_CACHE_BYTES:
                        while replayed and (
                            replayed_bytes + len(raw) > _REPLAY_CACHE_BYTES
                            or len(replayed) >= _REPLAY_CACHE_ENTRIES
                        ):
                            old_raw, _ = replayed.popitem(last=False)
                            replayed_bytes -= len(old_raw)
                        replayed[raw] = (capture, qualification)
                        replayed_bytes += len(raw)''', 1)
assert blob(text.encode()) == CANDIDATE_BLOB
SOURCE.write_text(text)

# Normal package bootstrap and real validation code, never substituted modules.
network_attempts = 0
def forbid_network(*args, **kwargs):
    global network_attempts
    network_attempts += 1
    raise AssertionError("offline comparison attempted network access")
import requests
requests.sessions.Session.request = forbid_network
socket.socket.connect = forbid_network
socket.socket.connect_ex = forbid_network
socket.create_connection = forbid_network

import concierge.bounty_capture_recover as candidate
from concierge.bounty_capture import capture_digest, replay_capture, _issue_projection
from concierge.bounty_preflight import (
    _apply_assignee_gate, _apply_maintainer_contribution_pause_gate,
)
from concierge.bounty_qualification import qualify_dispatch
from concierge.credential_safety import apply_credential_gate, credential_gate_signal_types

# Baseline shares the unchanged package dependencies with the candidate.
with tempfile.TemporaryDirectory(prefix="copperfin-recovery-", dir=ROOT) as temp:
    root = Path(temp)
    baseline_path = root / "baseline.py"
    baseline_path.write_bytes(base_raw)
    spec = importlib.util.spec_from_file_location("recovery_baseline", baseline_path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)

    def fixture(number=1, state="closed"):
        repo = "synthetic/recovery-fixture"
        audit = {"issue_state": state, "open_pr_count": 0,
                 "stale_listing_signal": False, "search_truncated": False}
        base = {"title": "Synthetic recovery fixture", "body": "Offline fixture prose. " * 200,
                "labels": [], "attempt_count": 0, "canonical_audit": audit}
        q = qualify_dispatch({"repo": repo, "submission_target": None, **base}, saturation_threshold=4)
        q = apply_credential_gate(q, credential_gate_signal_types([base["body"]]))
        q = _apply_maintainer_contribution_pause_gate(q, 0)
        q = _apply_assignee_gate(q, {"formal_assignee_count": 0,
                                   "assigned_to_operator": False, "foreign_assignee_count": 0})
        assert q["dispatch"] is False
        issue = f"https://api.github.com/repos/{repo}/issues/{number}"
        value = {
            "schema": "bounty-preflight-capture/v1", "repo": repo, "number": number,
            "issue_url": f"https://github.com/{repo}/issues/{number}",
            "policy": {"max_pages": 1, "saturation_threshold": 4},
            "observation": {"started_at": "2026-10-01T00:00:00Z", "completed_at": "2026-10-01T00:00:01Z",
                            "request_count": 3, "source_urls": sorted([issue, issue + "/comments", "https://api.github.com/search/issues"])},
            "baseline": base, "authority": {"issue_author_association": "OWNER", "maintainer_comments": []},
            "assignment": {"formal_assignee_count": 0, "assigned_to_operator": False,
                           "foreign_assignee_count": 0, "principal_check": "NO_ASSIGNEES"},
            "generation": {"issue_sha256": "0" * 64, "issue_projection_sha256": "0" * 64,
                           "issue_updated_at": "2026-09-30T00:00:00Z", "issue_comment_count": 0,
                           "comments": [], "comments_truncated": False, "attempt_signal_count": 0},
            "checks": {"audit_before": None, "generation": None, "audit_after": None}, "qualification": q,
        }
        value["generation"]["issue_projection_sha256"] = capture_digest(_issue_projection(value))
        value["receipt_sha256"] = capture_digest(value)
        replay_capture(value, saturation_threshold=4)
        return value

    def wire(value):
        return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()

    a = fixture()
    b = fixture(2, "open")
    aw, bw = wire(a), wire(b)
    manifest = [{"repo": a["repo"], "number": 1}, {"repo": b["repo"], "number": 2}]
    clock_fields = {"recovery_started_at", "recovery_completed_at", "elapsed_seconds"}
    measurements = []
    serial = 0

    def compare(name, payloads, rows, expected_calls, repeats=1, expected_error=None):
        global serial
        samples = {"baseline": [], "candidate": []}
        counts = None
        for repeat in range(repeats):
            serial += 1
            src = root / f"source-{serial}"
            src.mkdir(mode=0o700)
            (src / "shortlist.json").write_text(json.dumps({"candidates": rows}))
            for index, payload in enumerate(payloads):
                p = src / f"capture-{index:04d}.json"
                p.write_bytes(payload)
                p.chmod(0o600)
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in src.iterdir()}
            results = {}
            counts = {}
            order = [("baseline", baseline), ("candidate", candidate)]
            if repeat % 2:
                order.reverse()
            for label, module in order:
                calls = [0]
                original = module.replay_capture
                def counted(*args, **kwargs):
                    calls[0] += 1
                    return original(*args, **kwargs)
                module.replay_capture = counted
                output = root / f"output-{serial}-{label}"
                start = time.perf_counter()
                try:
                    result = module.recover_batch(src, output)
                finally:
                    module.replay_capture = original
                samples[label].append(time.perf_counter() - start)
                assert result["request_count"] == 0
                assert output.stat().st_mode & 0o777 == 0o700
                normalized = {k: v for k, v in result.items() if k not in clock_fields}
                files = {}
                for p in output.iterdir():
                    assert p.stat().st_mode & 0o777 == 0o600
                    if p.name == "summary.json":
                        stored = json.loads(p.read_bytes())
                        assert stored == result
                    else:
                        files[p.name] = p.read_bytes()
                for index, payload in enumerate(payloads):
                    assert files[f"capture-{index:04d}.json"] == payload
                results[label] = (normalized, files)
                counts[label] = calls[0]
            assert results["baseline"] == results["candidate"], name
            assert tuple(counts[x] for x in ("baseline", "candidate")) == expected_calls, (name, counts)
            assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in src.iterdir()} == before
            if expected_error:
                codes = {item.get("error", {}).get("code") for item in results["candidate"][0]["items"]}
                assert expected_error in codes, (name, codes)
        measurements.append({"case": name, "replay_calls": counts,
                             "median_seconds": {k: statistics.median(v) for k, v in samples.items()},
                             "repetitions": repeats, "files": len(payloads), "equivalent": True})

    compare("single-capture", [aw], manifest[:1], (1, 1))
    compare("100-identical-captures", [aw] * 100, manifest[:1], (100, 1), repeats=5)
    compare("interleaved-duplicates", [aw, bw, aw, bw], manifest, (4, 2))
    bad = deepcopy(a); bad["baseline"]["body"] += "tampered"
    compare("same-receipt-altered-bytes", [aw, wire(bad), wire(bad), aw], manifest[:1], (4, 3), expected_error="CAPTURE_INVALID")
    conflict = deepcopy(a); conflict["observation"]["completed_at"] = "2026-10-01T00:00:02Z"
    del conflict["receipt_sha256"]; conflict["receipt_sha256"] = capture_digest(conflict)
    compare("conflicting-observations", [aw, wire(conflict), aw], manifest[:1], (3, 2), expected_error="CONFLICTING_CAPTURES")
    compare("invalid-json-retained", [b"{", aw, b"{"], manifest[:1], (1, 1), expected_error="CAPTURE_JSON_ERROR")
    compare("absent-from-shortlist-not-cached", [aw, aw], [], (2, 2), expected_error="CAPTURE_NOT_IN_SHORTLIST")
    compare("oversized-entry-not-cached", [aw + b" " * (1024 * 1024)] * 2, manifest[:1], (2, 2))
    big_a, big_b = aw + b" " * 600000, b" " * 600000 + aw
    compare("byte-budget-eviction", [big_a, big_b, big_a], manifest[:1], (3, 3))
    variants = [aw + b" " * i for i in range(129)]
    compare("entry-budget-eviction", variants + [variants[0]], manifest[:1], (130, 130))
    unique = [fixture(i) for i in range(1, 101)]
    compare("100-unique-issues", [wire(v) for v in unique],
            [{"repo": v["repo"], "number": v["number"]} for v in unique], (100, 100), repeats=3)
    compare("next-invocation-revalidates", [aw, aw], manifest[:1], (2, 1))
    assert network_attempts == 0
    report = {
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "baseline_blob": BASELINE_BLOB, "candidate_blob": CANDIDATE_BLOB,
        "python": platform.python_version(), "requests": requests.__version__,
        "cases_passed": len(measurements), "network_attempts": network_attempts,
        "fixture_scope": "synthetic valid closed/non-reward captures; no live provider observations or fleet throughput claim",
        "measurements": measurements,
    }
    (ROOT / "recovery-measurement.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
