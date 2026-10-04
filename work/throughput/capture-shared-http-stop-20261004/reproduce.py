#!/usr/bin/env python3
"""Exercise real batch/preflight/audit code over local HTTP; no provider calls."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import requests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from concierge import bounty_capture_batch as candidate

ROWS = [{"repo": "fixture/repo", "number": number} for number in range(1, 26)]
CASES = ("search403", "pull404", "issue404", "issue410", "cooldown429", "redirect404")


def blob(path):
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


@contextmanager
def provider(case):
    reads = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            reads.append(self.path)
            headers = {"X-RateLimit-Remaining": "50"}
            status, payload = 200, {}
            issue = re.fullmatch(r"/repos/fixture/repo/issues/(\d+)", parsed.path)
            if issue:
                number = int(issue.group(1))
                if case in {"issue404", "issue410"} and number == 1:
                    status, payload = int(case[-3:]), {"message": "Issue unavailable"}
                elif case == "cooldown429":
                    status, payload = 429, {"message": "Provider cooldown"}
                    headers.update({"Retry-After": "7", "X-RateLimit-Reset": "1893456000"})
                elif case == "redirect404":
                    status, payload = 302, {}
                    headers["Location"] = "https://api.github.com/search/issues"
                else:
                    payload = {
                        "number": number, "html_url": f"https://github.com/fixture/repo/issues/{number}",
                        "title": "Closed fixture bounty", "body": "$25 bounty.", "state": "closed",
                        "updated_at": "2026-10-04T00:00:00Z", "comments": 0,
                        "assignees": [], "labels": [], "author_association": "OWNER",
                    }
            elif parsed.path.endswith("/comments"):
                payload = []
            elif parsed.path == "/search/issues":
                if case == "search403":
                    status, payload = 403, {"message": "Resource not accessible by integration"}
                elif case == "redirect404":
                    status, payload = 404, {"message": "Search unavailable"}
                elif case == "pull404":
                    query = parse_qs(parsed.query)["q"][0]
                    number = int(query.rsplit(" ", 1)[1])
                    payload = {"items": [{"number": 100, "body": f"Fixes #{number}"}], "total_count": 1}
                else:
                    payload = {"items": [], "total_count": 0}
            elif parsed.path == "/repos/fixture/repo/pulls/100":
                status, payload = 404, {"message": "Pull request unavailable"}
            else:
                raise AssertionError(self.path)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()

    class LocalAdapter(requests.adapters.HTTPAdapter):
        def send(self, request, **kwargs):
            original = request.url
            parsed = urlsplit(original)
            assert parsed.netloc == "api.github.com", original
            request.url = f"http://127.0.0.1:{server.server_port}{parsed.path}"
            if parsed.query:
                request.url += "?" + parsed.query
            try:
                response = super().send(request, **kwargs)
                response.url = original
                return response
            finally:
                request.url = original

    try:
        with requests.Session() as session:
            session.trust_env = False
            session.mount("https://api.github.com/", LocalAdapter())
            yield session, reads
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def exercise(module, case, stops_shared):
    rows = ROWS if case == "search403" else ROWS[:2]
    with tempfile.TemporaryDirectory() as directory, provider(case) as (session, reads):
        output = Path(directory) / "capture"
        result = module.collect_batch(rows, output, token="fixture-only", session=session)
        remaining = json.loads((output / "remaining.json").read_text())["candidates"]
        supply = json.loads((output / "supply.json").read_text())["candidates"]
        assert result["request_count"] == len(reads), (case, result, reads)
        assert result["status"] == "PARTIAL" and not result["complete"]
        if case in {"issue404", "issue410"}:
            assert result["captured_count"] == 1 and remaining == rows[:1]
            assert supply[0]["number"] == 2 and result["stop_reason"] == "ITEM_ERRORS"
            assert len(reads) == 4
        else:
            assert remaining == rows and supply == []
            expected = {"search403": 3, "pull404": 4, "cooldown429": 1, "redirect404": 2}[case]
            if not stops_shared and case != "cooldown429":
                expected *= len(rows)
            assert len(reads) == expected, (case, len(reads), expected, result)
            assert result["attempted_count"] == (1 if stops_shared or case == "cooldown429" else len(rows))
        if case == "cooldown429":
            assert result["stop_reason"] == "RATE_LIMITED" and result["rate_limited"] is True
            assert result["retry_after_seconds"] == 7 and result["rate_limit_reset_at"] == 1893456000
        else:
            assert result["rate_limited"] is False
            assert result["retry_after_seconds"] is None and result["rate_limit_reset_at"] is None
            if stops_shared and case not in {"issue404", "issue410"}:
                assert result["stop_reason"] == "HTTP_ERROR"
        assert "request_url" not in json.dumps(result)
        return {key: result[key] for key in (
            "request_count", "attempted_count", "captured_count", "remaining_count",
            "stop_reason", "rate_limited", "retry_after_seconds", "rate_limit_reset_at",
        )}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    results = {"python": sys.version.split()[0], "requests": requests.__version__,
               "candidate_blob": blob(candidate.__file__), "cases": {}}
    baseline = None
    if args.baseline:
        spec = importlib.util.spec_from_file_location("capture_batch_baseline", args.baseline)
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        results["baseline_blob"] = blob(args.baseline)
    for case in CASES:
        row = {}
        if baseline is not None:
            row["before"] = exercise(baseline, case, False)
        row["after"] = exercise(candidate, case, True)
        results["cases"][case] = row
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
