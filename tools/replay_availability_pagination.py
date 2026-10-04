#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Replay the real availability reader against synthetic loopback HTTP pages.

Usage: python tools/replay_availability_pagination.py [--source PATH] [--baseline]
Only the deployment token config is replaced with None. The complete selected
module is executed, including its classifier and generation checks; no provider,
credentials, repository bootstrap hooks, or account state are contacted.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import ModuleType
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import requests

REPO = "synthetic/availability"
ISSUE = "/repos/synthetic/availability/issues/1"
COMMENTS = ISSUE + "/comments"
WHEN = "2026-10-04T00:00:00Z"


@dataclass
class Case:
    name: str
    count: int
    expected: str
    calls: int
    baseline_expected: str | None = None
    baseline_calls: int | None = None
    max_pages: int = 10
    page_size: int = 100
    change: str = ""
    malformed_link: str | None = None


def load_module(source: Path):
    # Load the complete source without importing the unrelated package policy
    # bootstrap. Replacing the token config is the sole import substitution.
    config = ModuleType("concierge.config")
    config.GITHUB_TOKEN = None
    spec = importlib.util.spec_from_file_location("_availability_replay_subject", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load source: {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with patch.dict(sys.modules, {"concierge.config": config}):
        spec.loader.exec_module(module)
    return module


def run_case(module, case: Case) -> dict:
    rows = [{"id": index + 1, "created_at": WHEN, "updated_at": WHEN,
             "author_association": "OWNER", "body": "Implementation discussion.",
             "user": {"login": "synthetic-owner", "type": "User"}}
            for index in range(case.count)]
    if case.change == "terminal" and rows:
        rows[-1]["body"] = "No more submissions."
    if case.change == "duplicate" and len(rows) > 1:
        rows[-1] = deepcopy(rows[-2])
    issue = {"id": 1, "number": 1, "state": "open", "updated_at": WHEN,
             "comments": case.count, "title": "Synthetic bounty", "body": "Synthetic terms"}
    if case.change == "closed":
        issue["state"] = "closed"
    if case.change == "count":
        issue["comments"] += 1
    state = {"paths": [], "issue_reads": 0, "scans": 0}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            state["paths"].append(self.path)
            headers = {}
            status = 200
            if parsed.path == ISSUE:
                state["issue_reads"] += 1
                payload = deepcopy(issue)
                if case.change == "issue" and state["issue_reads"] == 2:
                    payload["updated_at"] = "2026-10-04T00:01:00Z"
            elif parsed.path == COMMENTS:
                params = parse_qs(parsed.query)
                if params.get("per_page") != ["100"]:
                    raise AssertionError("the production page size changed")
                page = int(params["page"][0])
                if page == 1:
                    state["scans"] += 1
                offset = (page - 1) * case.page_size
                payload = deepcopy(rows[offset:offset + case.page_size])
                if case.change == "body" and state["scans"] == 2 and payload:
                    payload[-1]["body"] = "A different discussion."
                links = []
                if offset + case.page_size < len(rows):
                    # A deliberately foreign destination proves the reader only
                    # consumes the relation, never redirects credentials to it.
                    destination = ("https://not-a-provider.invalid" if case.change == "foreign"
                                   else "https://api.github.com")
                    links.append(f'<{destination}{COMMENTS}?per_page=100&page={page + 1}>; rel="next"')
                if page > 1:
                    links.append(f'<https://api.github.com{COMMENTS}?per_page=100&page={page - 1}>; rel="prev"')
                if links:
                    headers["Link"] = ", ".join(links)
                if case.malformed_link is not None:
                    headers["Link"] = case.malformed_link
                if case.change == "429":
                    status = 429
                    payload = {"message": "Synthetic rate limit"}
                    headers["Retry-After"] = "60"
            else:
                raise AssertionError(f"unexpected path: {self.path}")
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    local_origin = f"http://127.0.0.1:{server.server_port}"

    class LoopbackSession(requests.Session):
        def __init__(self):
            super().__init__()
            self.trust_env = False

        def request(self, method, url, **kwargs):
            parsed = urlsplit(url)
            if method.lower() != "get" or parsed.scheme != "https" or parsed.netloc != "api.github.com":
                raise AssertionError(f"unexpected outbound request: {method} {url}")
            if parsed.path not in (ISSUE, COMMENTS) or parsed.query or parsed.fragment:
                raise AssertionError(f"unexpected request target: {url}")
            if "Authorization" in kwargs.get("headers", {}):
                raise AssertionError("the replay must not use credentials")
            return super().request(method, local_origin + parsed.path, **kwargs)

    try:
        with LoopbackSession() as session:
            try:
                result = module.inspect_bounty_availability(REPO, 1, session=session, max_pages=case.max_pages)
                outcome = result["reason_code"] or result["disposition"]
            except module.BountyAvailabilityError:
                outcome = "ERROR"
                result = None
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    return {"case": case.name, "outcome": outcome, "requests": len(state["paths"]),
            "paths": state["paths"], "receipt": result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "concierge/bounty_availability.py")
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args(argv)
    module = load_module(args.source)
    cases = [
        Case("empty", 0, "CLEAR", 4),
        Case("one", 1, "CLEAR", 4),
        Case("full-terminal-100", 100, "CLEAR", 4, baseline_calls=6),
        Case("full-terminal-200", 200, "CLEAR", 6, baseline_calls=8),
        Case("full-terminal-100-cap1", 100, "CLEAR", 4, "COMMENT_HISTORY_TRUNCATED", 2, max_pages=1),
        Case("full-terminal-200-cap2", 200, "CLEAR", 6, "COMMENT_HISTORY_TRUNCATED", 3, max_pages=2),
        Case("nonterminal-101-cap1", 101, "COMMENT_HISTORY_TRUNCATED", 2, max_pages=1),
        Case("short-page-next", 100, "CLEAR", 6, "COMMENT_COUNT_MISMATCH", 4, page_size=50),
        Case("terminal-maintainer", 100, "MAINTAINER_TERMINAL_OUTCOME", 4, baseline_calls=6, change="terminal"),
        Case("known-closed", 100, "ISSUE_NOT_OPEN", 1, change="closed"),
        Case("changed-comment", 100, "COMMENT_GENERATION_CHANGED", 4, baseline_calls=6, change="body"),
        Case("changed-issue", 100, "ISSUE_GENERATION_CHANGED", 4, baseline_calls=6, change="issue"),
        Case("count-mismatch", 100, "COMMENT_COUNT_MISMATCH", 4, baseline_calls=6, change="count"),
        Case("duplicate-comment", 100, "COMMENT_ID_DUPLICATED", 4, baseline_calls=6, change="duplicate"),
        Case("foreign-next-not-followed", 101, "CLEAR", 6, change="foreign"),
        Case("rate-limit-no-retry", 100, "ERROR", 2, change="429"),
    ]
    if not args.baseline:
        cases += [
            Case("malformed-link", 1, "ERROR", 2, malformed_link="not a pagination link"),
            Case("dangling-link", 1, "ERROR", 2, malformed_link='<https://api.github.com/x>; rel="prev",'),
        ]
    observations = []
    for case in cases:
        row = run_case(module, case)
        expected = (case.baseline_expected or case.expected) if args.baseline else case.expected
        calls = (case.baseline_calls if case.baseline_calls is not None else case.calls) if args.baseline else case.calls
        if row["outcome"] != expected or row["requests"] != calls:
            raise AssertionError(f"{case.name}: expected {expected}/{calls}, observed {row}")
        observations.append(row)
    if not args.baseline:
        class WithoutHeaders:
            pass
        if not module._comment_page_has_next(WithoutHeaders(), [{}] * 100):
            raise AssertionError("metadata-free full-page fallback changed")
        if module._comment_page_has_next(WithoutHeaders(), []):
            raise AssertionError("metadata-free short-page fallback changed")
    data = args.source.read_bytes()
    print(json.dumps({"source_git_blob": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest(),
                      "source_sha256": hashlib.sha256(data).hexdigest(), "python": sys.version.split()[0],
                      "requests": requests.__version__, "baseline": args.baseline,
                      "cases_passed": len(cases), "observations": observations}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
