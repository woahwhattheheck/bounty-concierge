#!/usr/bin/env python3
"""Compare the real audit CLI using retained HTTP responses, with no network."""
from __future__ import annotations

import argparse
import contextlib
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import platform
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests
from requests.adapters import BaseAdapter


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(module, rows, failures=None, *, pr_failure=False, text=False):
    calls = []
    failures = failures or {}
    original_session = requests.Session

    class RetainedResponses(BaseAdapter):
        def send(self, request, **kwargs):
            parsed = urlsplit(request.url)
            path = parsed.path.casefold()
            calls.append(path)
            status, headers = 200, {}
            if path in failures:
                spec = failures[path]
                if spec == "timeout":
                    raise requests.Timeout("controlled timeout")
                status, headers = spec
                payload = {"message": "controlled HTTP failure"}
            elif path == "/search/issues":
                query = parse_qs(parsed.query)["q"][0]
                items = [{"number": 99, "body": "Fixes #2"}] if pr_failure and query.endswith(" 2") else []
                payload = {"total_count": len(items), "incomplete_results": False, "items": items}
            elif path.endswith("/comments"):
                payload = []
            else:
                number = int(path.rsplit("/", 1)[1])
                payload = {"number": number, "state": "open", "title": f"Issue {number}",
                           "body": "", "html_url": f"https://github.com/example/project/issues/{number}"}
            response = requests.Response()
            response.status_code = status
            response.url = request.url
            response.request = request
            response.headers = {"Content-Type": "application/json", **headers}
            response._content = json.dumps(payload).encode()
            return response

        def close(self):
            pass

    def session():
        value = original_session()
        value.trust_env = False
        value.mount("https://api.github.com/", RetainedResponses())
        return value

    with tempfile.TemporaryDirectory(prefix="audit-unavailable-") as directory:
        source = Path(directory) / "shortlist.json"
        source.write_text(json.dumps(rows), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        args = ["--batch", str(source)] + ([] if text else ["--json"])
        with patch.object(requests, "Session", session), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = module.main(args)
        return {"exit_code": code, "requests": len(calls), "calls": calls,
                "output": stdout.getvalue() if text else json.loads(stdout.getvalue()),
                "stderr": stderr.getvalue()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, default=Path("concierge/bounty_audit.py"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.candidate.resolve().parents[1]))
    network_events = []

    def reject_network(event, arguments):
        if event in {"socket.connect", "socket.getaddrinfo"}:
            network_events.append(event)
            raise RuntimeError("This replay must not contact a provider")

    sys.addaudithook(reject_network)
    baseline = load(args.baseline.resolve(), "audit_unavailable_baseline")
    candidate = load(args.candidate.resolve(), "audit_unavailable_candidate")
    rows = [{"repo": "example/project", "number": n} for n in (1, 2, 3)]
    issue2 = "/repos/example/project/issues/2"
    records, checks = {}, []
    for status in (404, 410):
        failures = {issue2: (status, {"X-RateLimit-Remaining": "12", "X-RateLimit-Reset": "2000000000"})}
        before, after = run(baseline, rows, failures), run(candidate, rows, failures)
        resumed = run(baseline, before["output"]["remaining_candidates"], failures)
        report = after["output"]
        assert (before["requests"], before["output"]["audited_count"], resumed["requests"], resumed["output"]["audited_count"]) == (4, 1, 1, 0)
        assert (after["exit_code"], after["requests"], report["audited_count"], report["unavailable_count"], report["remaining_count"]) == (2, 7, 2, 1, 0)
        assert report["traversal_complete"] and report["unavailable_candidates"][0]["candidate"] == rows[1]
        assert [row["number"] for row in report["rows"]] == [1, 3]
        assert "Provider cooldown metadata" not in after["stderr"]
        records[f"middle_{status}"] = {"baseline": before, "baseline_remaining_resume": resumed, "candidate": after}
        checks.append(f"middle_{status}_continues_without_fabricating_an_audit")

    controls = {
        "success": {},
        "authentication_401": {issue2: (401, {})},
        "throttle_429": {issue2: (429, {"Retry-After": "60"})},
        "throttle_403": {issue2: (403, {"Retry-After": "60", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "2000000000"})},
        "permission_403": {issue2: (403, {})},
        "server_503": {issue2: (503, {})},
        "timeout": {issue2: "timeout"},
        "unavailable_with_retry_after": {issue2: (404, {"Retry-After": "1"})},
        "unavailable_with_exhausted_quota": {issue2: (404, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "2000000000"})},
        "comments_404": {issue2 + "/comments": (404, {})},
        "pull_request_404": {"/repos/example/project/pulls/99": (404, {})},
    }
    for name, failures in controls.items():
        options = {"pr_failure": name == "pull_request_404"}
        before, after = run(baseline, rows, failures, **options), run(candidate, rows, failures, **options)
        assert before == after, name
        checks.append(f"unchanged_{name}")
        records[name] = {"requests": after["requests"], "exit_code": after["exit_code"],
                         "output_sha256": hashlib.sha256(json.dumps(after["output"], sort_keys=True).encode()).hexdigest()}

    five = [{"repo": "example/project", "number": n} for n in (1, 2, 3, 4, 5)]
    failures = {issue2: (404, {}), "/repos/example/project/issues/4": (429, {"Retry-After": "60"})}
    later = run(candidate, five, failures)
    report = later["output"]
    assert (later["requests"], report["audited_count"], report["unavailable_count"], report["failed_row"]) == (8, 2, 1, 4)
    assert not report["traversal_complete"] and [row["number"] for row in report["remaining_candidates"]] == [4, 5]
    assert report["error"]["retry_after"] == "60"
    records["unavailable_then_throttle"] = later
    checks.append("later_shared_failure_retains_correct_remaining_rows_and_cooldown")

    target = {"repository": "example/delivery", "source_url": "https://github.com/example/project/issues/2",
              "source_content_sha256": "a" * 64, "instruction_excerpt": "Submit code to example/delivery."}
    unavailable = {**rows[1], "submission_target": target, "listing_id": "retained-example", "canonical_audit": {"old": True}}
    duplicates = [rows[0], unavailable, {**deepcopy(unavailable), "repo": "Example/Project"}, rows[2]]
    original = deepcopy(duplicates)
    duplicate = run(candidate, duplicates, {issue2: (404, {})})
    outcomes = duplicate["output"]["unavailable_candidates"]
    assert duplicates == original and duplicate["requests"] == 7 and len(outcomes) == 2
    assert all("canonical_audit" not in row["candidate"] and row["candidate"]["submission_target"] == target for row in outcomes)
    assert outcomes[1]["candidate"]["repo"] == "Example/Project"
    records["duplicate_unavailable_with_explicit_target"] = duplicate
    checks.append("duplicate_unavailable_read_once_and_input_metadata_preserved")
    rendered = run(candidate, rows, {issue2: (404, {})}, text=True)
    assert rendered["exit_code"] == 2 and "traversal completed" in rendered["stderr"]
    assert "example/project#2 HTTP 404" in rendered["stderr"]
    checks.append("text_output_exposes_unavailable_issue_and_partial_status")

    result = {"python": platform.python_version(), "requests_version": requests.__version__,
              "baseline_git_blob": git_blob(args.baseline), "candidate_git_blob": git_blob(args.candidate),
              "measurement": "Full production module and CLI with Requests prepared requests, status/JSON handling, and retained-response transport; no live-fleet latency or savings claim.",
              "passed_check_groups": len(checks), "checks": checks, "provider_network_attempts": len(network_events),
              "records": records}
    assert not network_events
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"passed_check_groups": len(checks), "provider_network_attempts": 0, "output": str(args.output)}))


def git_blob(path):
    raw = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


if __name__ == "__main__":
    main()
