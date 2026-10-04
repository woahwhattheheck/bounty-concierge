#!/usr/bin/env python3
"""Replay batch HTTP-error cleanup using Requests and a local HTTP/1.1 server."""
from __future__ import annotations

import argparse
from contextlib import suppress
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import urlsplit

import requests


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        scenario, _, path = self.path.lstrip("/").partition("/")
        path = "/" + path.split("?", 1)[0]
        headers = {}
        status, payload = 200, {}
        if path == "/repos/fixture/batch/issues/1" or path == "/cleanup-error":
            status = 429 if scenario == "quota" else 404
            payload = {"message": "fixture error"}
            if status == 429:
                headers = {"Retry-After": "17", "X-RateLimit-Remaining": "0",
                           "X-RateLimit-Reset": "1791111111"}
        elif path.endswith("/comments"):
            payload = []
        elif path == "/search/issues":
            payload = {"total_count": 0, "incomplete_results": False, "items": []}
        elif path == "/repos/fixture/batch/issues/2":
            payload = {
                "id": 2, "node_id": "fixture-issue-2", "number": 2,
                "state": "closed", "title": "[BOUNTY $100] fixture",
                "body": "A $100 USD fixed-price bounty.", "comments": 0,
                "updated_at": "2026-10-04T00:00:00Z", "labels": [], "assignees": [],
                "author_association": "OWNER", "user": {"login": "owner", "type": "User"},
                "html_url": "https://github.com/fixture/batch/issues/2",
                "url": "https://api.github.com/repos/fixture/batch/issues/2",
            }
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # Closing an unread fixture body may reset its keep-alive connection.


class Session(requests.Session):
    """Use real Requests; redirect this fixture's canonical URLs to loopback."""

    def __init__(self, base, scenario):
        super().__init__()
        self.trust_env = False
        self.base, self.scenario = base, scenario
        self.responses, self.errors = [], []
        self.close_count = 0
        self.hooks["response"].append(self.observe)

    def request(self, method, url, **kwargs):
        parsed = urlsplit(url)
        return super().request(method, self.base + "/" + self.scenario + parsed.path, **kwargs)

    def observe(self, response, **kwargs):
        response.fixture_close_count = 0
        original_close = response.close

        def close():
            response.fixture_close_count += 1
            original_close()
            if self.scenario == "cleanup":
                raise RuntimeError("fixture cleanup failure")

        response.close = close
        self.responses.append(response)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            self.errors.append(exc)
            raise

    def close(self):
        self.close_count += 1
        super().close()

    def dispose(self):
        for response in self.responses:
            with suppress(Exception):
                response.close()
        self.close()


def failed_responses(session):
    return [{"close_count": exc.response.fixture_close_count,
             "raw_closed": exc.response.raw.closed,
             "connection_released": exc.response.raw._connection is None}
            for exc in session.errors]


def replay(module):
    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    base = "http://127.0.0.1:" + str(server.server_port)
    results = {}
    try:
        for scenario in ("continue", "quota"):
            session = Session(base, scenario)
            try:
                with tempfile.TemporaryDirectory(prefix="batch-http-release-") as temporary:
                    summary = module.collect_batch(
                        [{"repo": "fixture/batch", "number": 1},
                         {"repo": "fixture/batch", "number": 2}],
                        Path(temporary) / "batch", session=session,
                    )
                results[scenario] = {
                    "summary": {key: summary[key] for key in (
                        "status", "stop_reason", "attempted_count", "captured_count",
                        "failed_count", "remaining_count", "request_count", "rate_limited",
                        "retry_after_seconds", "rate_limit_reset_at")},
                    "failed_responses": failed_responses(session),
                    "caller_session_close_count": session.close_count,
                }
            finally:
                session.dispose()
        session = Session(base, "ownership")
        try:
            response = module._BatchSession(session, 10).get(
                "https://api.github.com/ownership", stream=True, timeout=2)
            results["returned_response"] = {
                "same_response": response is session.responses[-1],
                "close_count": response.fixture_close_count,
                "raw_closed": response.raw.closed,
                "body_consumed": response._content_consumed,
                "caller_session_close_count": session.close_count,
            }
        finally:
            session.dispose()
        session = Session(base, "cleanup")
        try:
            transport = module._BatchSession(session, 10)
            try:
                transport.get("https://api.github.com/cleanup-error", timeout=2)
            except requests.HTTPError as exc:
                results["cleanup_failure"] = {
                    "same_original_exception": exc is session.errors[-1],
                    "failure": transport.failure, "request_count": transport.request_count,
                    "failed_responses": failed_responses(session),
                }
        finally:
            session.dispose()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    module = importlib.import_module("concierge.bounty_capture_batch")
    source = Path(module.__file__).read_bytes()
    print(json.dumps({
        "source_blob": hashlib.sha1(b"blob " + str(len(source)).encode() + b"\0" + source).hexdigest(),
        "requests_version": requests.__version__, "cases": replay(module),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
