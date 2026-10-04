# SPDX-License-Identifier: MIT
"""Replay real Requests response hooks against a loopback-only catalog server.

Usage: python replay.py /path/to/concierge/bountyhub_catalog.py
Exit 1 means the supplied source still fails at least one expectation.
"""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
from threading import Thread
from unittest.mock import Mock

import requests


def listing(number: int) -> dict:
    return {
        "id": f"00000000-0000-0000-0000-{number:012d}",
        "repositoryFullName": "example/project", "issueNumber": number,
        "htmlURL": f"https://github.com/example/project/issues/{number}",
        "title": "Synthetic fixture", "issueState": "open",
        "assignmentType": "OPEN", "assignee": None, "claimed": False,
        "retracted": False, "solved": False, "isFrozen": False,
        "deletedAt": None, "totalAmount": "100.00", "claims": [],
        "pledges": [{"amount": "100.00", "paymentStatus": "PAID",
                     "retracted": False, "deletedAt": None, "isPaid": False}],
    }


def main() -> int:
    path = Path(sys.argv[1]).resolve()
    spec = importlib.util.spec_from_file_location("catalog_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = [
        ("catalog_429", [(429, {"Retry-After": "7"}, {})],
         {"status": 429, "delay": 7, "throttled": True, "requests": 1}),
        ("catalog_403_retry", [(403, {"Retry-After": "11"}, {})],
         {"status": 403, "delay": 11, "throttled": True, "requests": 1}),
        ("catalog_403_permission", [(403, {}, {})],
         {"status": 403, "delay": None, "throttled": False, "requests": 1}),
    ]
    page = {"data": [listing(1), listing(2)], "hasNextPage": False}
    for status in (404, 410, 429, 500):
        continued = status in (404, 410)
        cases.append((f"detail_{status}",
                      [(200, {}, page), (status, {"Retry-After": "7"}, {}),
                       (200, {}, listing(2))],
                      {"status": status, "delay": 7,
                       "throttled": status == 429,
                       "requests": 3 if continued else 2,
                       "funding": ["READ_FAILED", "COMPLETE" if continued else "NOT_ATTEMPTED"]}))
    cases.append(("success", [(200, {}, {"data": [], "hasNextPage": False})],
                  {"status": None, "delay": None, "throttled": False, "requests": 1}))
    results = []
    active = {"replies": [], "paths": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            active["paths"].append(self.path)
            status, headers, payload = active["replies"].pop(0)
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        module.API = f"http://127.0.0.1:{server.server_port}/api/bounties"
        try:
            for name, replies, expected in cases:
                active.update(replies=list(replies), paths=[])
                observed = []

                def hook(response, **kwargs):
                    response.close = Mock(wraps=response.close)
                    observed.append(response)
                    response.raise_for_status()
                    return response

                with requests.Session() as session:
                    session.trust_env = False
                    session.hooks["response"].append(hook)
                    try:
                        report = module.fetch_catalog(session=session, max_pages=1, max_details=2)
                        error = report["errors"][0] if report["errors"] else {}
                        actual = {
                            "status": error.get("http_status"),
                            "delay": report["retry_after_seconds"],
                            "throttled": report["rate_limited"],
                            "requests": report["requests_made"],
                        }
                        if "funding" in expected:
                            actual["funding"] = [row["funding_status"] for row in report["listings"]]
                        close_counts = [response.close.call_count for response in observed]
                        passed = (actual == expected
                                  and close_counts == [1] * len(observed)
                                  and len(active["paths"]) == expected["requests"]
                                  and report["complete"] == (name == "success")
                                  and (not error or error["code"] == "HTTP_ERROR"))
                        results.append({"case": name, "pass": passed, "actual": actual,
                                        "expected": expected, "response_close_counts": close_counts,
                                        "http_requests_observed": len(active["paths"]),
                                        "error_code": error.get("code")})
                    finally:
                        # Clean up baseline leaks AFTER measuring source ownership.
                        for response in observed:
                            if not response.close.called:
                                response.close()
        finally:
            server.shutdown()
            thread.join()

    class NoResponse:
        def get(self, *args, **kwargs):
            raise requests.HTTPError("synthetic transport error without a response")

    report = module.fetch_catalog(session=NoResponse(), max_pages=1)
    error = report["errors"][0]
    results.append({"case": "http_error_without_response", "pass": (
        error["code"] == "HTTPError" and error["http_status"] is None
        and not report["rate_limited"] and report["requests_made"] == 1)})
    source = path.read_bytes()
    output = {
        "source_blob": hashlib.sha1(b"blob " + str(len(source)).encode() + b"\0" + source).hexdigest(),
        "python": sys.version.split()[0], "requests": requests.__version__,
        "network_scope": "loopback only; synthetic provider fixtures",
        "passed": sum(row["pass"] for row in results), "total": len(results),
        "cases": results,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if all(row["pass"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
