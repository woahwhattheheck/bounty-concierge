#!/usr/bin/env python3
"""Compare real audit source against a bounded native Requests pool stall.

Run with --baseline /path/to/earlier/bounty_audit.py. Nine exact source AST
definitions execute; unrelated CLI/config/submission-target imports are omitted.
The pool exercise uses native Requests, HTTPAdapter and a loopback HTTP/1.1
server. Two small injected-error controls check metadata and cleanup failures.
"""
from __future__ import annotations

import argparse
import ast
from collections.abc import Mapping
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import platform
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import requests


NAMES = {
    "BountyAuditError", "_headers", "_get_json", "_object_payload",
    "_competition_level", "audit_bounty", "_batch_error_details",
    "_batch_partial_report", "audit_bounties",
}


def load_source(path: Path) -> tuple[dict, dict]:
    data = path.read_bytes()
    tree = ast.parse(data, filename=str(path))
    selected = [node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in NAMES]
    if {node.name for node in selected} != NAMES:
        raise ValueError("source does not contain the expected audit definitions")
    scope = {"Any": Any, "Mapping": Mapping, "requests": requests,
             "deepcopy": deepcopy, "json": json, "GITHUB_TOKEN": None}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), scope)
    identity = {"path": str(path), "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "git_blob_sha": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()}
    return scope, identity


def pool_probe(scope: dict) -> dict:
    wire_paths, captured = [], []
    attempted_second, finished = threading.Event(), threading.Event()
    result = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            wire_paths.append(path)
            if path.endswith("/issues/1"):
                status, payload = 404, {"message": "Not Found"}
            elif path.endswith("/issues/2"):
                status, payload = 200, {"state": "closed", "html_url": "https://github.com/Example/repo/issues/2"}
            elif path == "/search/issues":
                status, payload = 200, {"items": [], "total_count": 0, "incomplete_results": False}
            else:
                status, payload = 500, {"message": "unexpected local request"}
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-RateLimit-Remaining", "4999")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            pass  # Closing the intentionally unread response can reset keep-alive.

    server = Server(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever,
                                     kwargs={"poll_interval": 0.05}, daemon=True)
    server_thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    class LocalAdapter(requests.adapters.HTTPAdapter):
        def send(self, request, **kwargs):
            parsed = urlsplit(request.url)
            assert parsed.scheme == "https" and parsed.netloc == "api.github.com"
            if parsed.path.endswith("/issues/2"):
                attempted_second.set()
            request.url = origin + parsed.path + ("?" + parsed.query if parsed.query else "")
            return super().send(request, **kwargs)

    session = requests.Session()
    session.trust_env = False
    session.mount("https://api.github.com/", LocalAdapter(pool_connections=1,
                  pool_maxsize=1, pool_block=True, max_retries=0))

    def raise_status(response, *args, **kwargs):
        captured.append(response)
        response.raise_for_status()

    session.hooks["response"].append(raise_status)

    def run():
        try:
            scope["audit_bounties"]([
                {"repo": "Example/repo", "number": 1},
                {"repo": "Example/repo", "number": 2},
            ], session=session)
            result["unexpected_success"] = True
        except scope["BountyAuditError"] as error:
            result.update(http_status=error.http_status,
                          cause_type=type(error.__cause__).__name__,
                          rate_limit_remaining=error.rate_limit_remaining,
                          partial_report=error.partial_report)
        except BaseException as error:
            result["unexpected_error"] = repr(error)
        finally:
            finished.set()

    worker = threading.Thread(target=run, daemon=True)
    started = time.perf_counter()
    worker.start()
    try:
        assert attempted_second.wait(2), "batch did not attempt its independent second issue"
        completed_without_rescue = finished.wait(0.25)
        before_rescue = {"completed_without_rescue": completed_without_rescue,
                         "wire_requests": len(wire_paths),
                         "first_response_closed": captured[0].raw.closed,
                         "first_response_content_consumed": captured[0]._content_consumed}
        rescue_started = time.perf_counter()
        if not completed_without_rescue:
            captured[0].close()
        assert finished.wait(3), "worker did not finish after releasing the first response"
        worker.join(timeout=0.1)
        result.update(before_rescue=before_rescue, wire_paths=wire_paths,
                      rescue_ms=(time.perf_counter() - rescue_started) * 1000,
                      total_ms=(time.perf_counter() - started) * 1000)
        # Only the ephemeral fixture origin differs in native HTTPError text.
        result["normalized_partial_report"] = json.loads(
            json.dumps(result.get("partial_report")).replace(origin, "http://loopback"))
        return result
    finally:
        for response in captured:
            response.close()
        session.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1)


def cooldown_control(scope: dict, cleanup_raises: bool) -> dict:
    response = requests.Response()
    response.status_code = 429
    response.headers.update({"Retry-After": "7", "X-RateLimit-Reset": "2000000000",
                             "X-RateLimit-Remaining": "0"})
    response._content, response._content_consumed = b"{}", True
    provider_error = requests.HTTPError("controlled 429", response=response)
    close_calls = []
    original_close = response.close

    def close():
        close_calls.append(1)
        if cleanup_raises:
            raise RuntimeError("controlled close failure")
        original_close()

    response.close = close

    class FailedSession:
        def get(self, *args, **kwargs):
            raise provider_error

    try:
        scope["_get_json"](FailedSession(), "https://api.github.com/repos/Example/repo/issues/1", headers={})
    except scope["BountyAuditError"] as error:
        return {"http_status": error.http_status, "retry_after": error.retry_after,
                "rate_limit_reset": error.rate_limit_reset,
                "rate_limit_remaining": error.rate_limit_remaining,
                "original_cause_preserved": error.__cause__ is provider_error,
                "close_calls": len(close_calls), "cleanup_raises": cleanup_raises}
    raise AssertionError("provider failure was not preserved")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path,
                        default=Path(__file__).resolve().parents[1] / "concierge" / "bounty_audit.py")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {"schema_version": "audit-http-response-release/v1", "python": platform.python_version(),
              "requests": requests.__version__, "source_definitions": sorted(NAMES),
              "scope": "Nine exact source AST definitions. Config token unset; unrelated CLI/target/comment imports not exercised. Native Requests/HTTPAdapter/HTTP1.1 pool; canonical URLs map exclusively to loopback. Controls inject native Requests HTTPError/Response objects; cleanup failure is deliberately injected. Time includes a forced baseline observation interval and is not a throughput benchmark.",
              "sources": {}}
    for name, path in (("baseline", args.baseline), ("candidate", args.candidate)):
        scope, identity = load_source(path)
        report["sources"][name] = {"identity": identity, "pool": pool_probe(scope),
                                   "controls": [cooldown_control(scope, False), cooldown_control(scope, True)]}
    baseline, candidate = (report["sources"][name] for name in ("baseline", "candidate"))
    report["checks"] = {
        "baseline_stalled_after_one_get": baseline["pool"]["before_rescue"] == {
            "completed_without_rescue": False, "wire_requests": 1,
            "first_response_closed": False, "first_response_content_consumed": False},
        "candidate_completed_without_rescue": candidate["pool"]["before_rescue"] == {
            "completed_without_rescue": True, "wire_requests": 3,
            "first_response_closed": True, "first_response_content_consumed": False},
        "same_partial_outcome": baseline["pool"]["normalized_partial_report"] == candidate["pool"]["normalized_partial_report"],
        "same_requests_after_baseline_rescue": baseline["pool"]["wire_paths"] == candidate["pool"]["wire_paths"],
        "cooldown_and_cause_preserved": all(
            control == {"http_status": 429, "retry_after": "7", "rate_limit_reset": "2000000000",
                        "rate_limit_remaining": 0, "original_cause_preserved": True,
                        "close_calls": 0 if name == "baseline" else 1, "cleanup_raises": raises}
            for name, source in report["sources"].items()
            for raises, control in zip((False, True), source["controls"])),
    }
    report["passed"] = all(report["checks"].values())
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(text)
    print(text, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
