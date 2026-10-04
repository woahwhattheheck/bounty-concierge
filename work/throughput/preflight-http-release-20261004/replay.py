#!/usr/bin/env python3
"""Compare the exact prior helper and current helper using loopback HTTP only."""
from __future__ import annotations

import argparse
import ast
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import platform
import sys
from threading import Thread
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import requests
import urllib3
from concierge import bounty_preflight as preflight

EXPECTED_BEFORE = "cc78fff3f030c8d222030a4355201978e7b5cf2d"


def blob_id(data):
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def check(value, message):
    if not value:
        raise RuntimeError(message)


class Server(ThreadingHTTPServer):
    def handle_error(self, request, address):
        # The baseline deliberately closes an unread response during cleanup.
        pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.server.peers.append(self.client_address)
        status = 403 if self.path == "/limited" else 200
        body = (b'{"message":"secondary rate limit reached"}' if status == 403
                else b"not-json" if self.path == "/invalid" else b'{"ok":true}')
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/json")
        self.send_header("X-RateLimit-Remaining", "42")
        self.send_header("Link", '<https://example.invalid/next>; rel="next"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class StreamSession(requests.Session):
    def get(self, *args, **kwargs):
        kwargs["stream"] = True
        return super().get(*args, **kwargs)


def exercise(helper, hook=False, path="/limited"):
    server = Server(("127.0.0.1", 0), Handler)
    server.peers = []
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    session = StreamSession()
    session.trust_env = False
    adapter = requests.adapters.HTTPAdapter(pool_connections=1, pool_maxsize=1, pool_block=True)
    session.mount("http://", adapter)
    observed, failure, metadata = [], None, {}

    def response_hook(response, **kwargs):
        observed.append(response)
        if hook:
            response.raise_for_status()

    session.hooks["response"] = [response_hook]
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        try:
            payload = helper(session, url + path, headers={}, response_metadata=metadata)
        except preflight.BountyPreflightError as exc:
            failure = exc
            payload = None
        # Read the actual pool, not connection_from_url(), which can create a
        # differently keyed empty pool in current Requests TLS-context handling.
        pools = list(adapter.poolmanager.pools._container.values())
        check(len(pools) == 1, "expected exactly one transport pool")
        response = observed[0]
        result = {
            "available_pool_slots": pools[0].pool.qsize(),
            "response_closed": response.raw.closed,
            "body_buffered": response._content is not False,
            "original_error": type(failure.__cause__).__name__ if failure else None,
            "payload": payload,
            "link_preserved": metadata.get("link") == '<https://example.invalid/next>; rel="next"',
        }
        # Do not make a potentially blocking second baseline request. A returned
        # slot allows the real same-session second GET and TCP-reuse observation.
        if result["available_pool_slots"] == 1:
            check(helper(session, url + "/ok", headers={}) == {"ok": True}, "follow-up read failed")
            result["followup_succeeded"] = True
            result["same_tcp_connection"] = server.peers[0] == server.peers[1]
        else:
            result["followup_succeeded"] = None
            result["same_tcp_connection"] = None
        result["request_count"] = len(server.peers)
        if failure and path == "/limited":
            detail = preflight._http_error_result(failure)
            result["cooldown_body_preserved"] = bool(detail and detail["rate_limited"])
        return result
    finally:
        for response in observed:
            response.close()
        session.close()
        server.shutdown()
        server.server_close()
        worker.join()


def compatibility(helper):
    captured = preflight._CapturedIssueSession(None, "frozen", {"ok": True})
    check(helper(captured, "frozen", headers={}) == {"ok": True}, "capture replay changed")
    original = requests.HTTPError("original")

    class BrokenResponse:
        @property
        def content(self):
            raise requests.ConnectionError("drain failed")

        def close(self):
            raise OSError("close failed")

    original.response = BrokenResponse()

    def fail(*args, **kwargs):
        raise original

    try:
        helper(SimpleNamespace(get=fail), "unused", headers={})
    except preflight.BountyPreflightError as exc:
        check(exc.__cause__ is original, "cleanup replaced original exception")
    else:
        raise RuntimeError("HTTP failure disappeared")
    return {"frozen_response_without_close": True, "failed_drain_and_close_preserve_cause": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path, required=True)
    args = parser.parse_args()
    old = args.baseline_source.read_bytes()
    check(blob_id(old) == EXPECTED_BEFORE, "unexpected baseline source")
    function = next(node for node in ast.parse(old).body
                    if isinstance(node, ast.FunctionDef) and node.name == "_get_json")
    namespace = dict(vars(preflight))
    exec(compile(ast.Module(body=[function], type_ignores=[]), "baseline-helper", "exec"), namespace)
    helpers = {"before": namespace["_get_json"], "after": preflight._get_json}
    runs = {label: {
        "streamed_http_error": exercise(helper),
        "hook_http_error": exercise(helper, hook=True),
        "success": exercise(helper, path="/ok"),
        "invalid_json": exercise(helper, path="/invalid"),
    } for label, helper in helpers.items()}
    for name in ("streamed_http_error", "hook_http_error"):
        check(runs["before"][name]["available_pool_slots"] == 0, "baseline gap not reproduced")
        fixed = runs["after"][name]
        check(fixed["available_pool_slots"] == 1 and fixed["response_closed"], "response not released")
        check(fixed["cooldown_body_preserved"] and fixed["original_error"] == "HTTPError", "diagnostics changed")
        check(fixed["followup_succeeded"] and fixed["same_tcp_connection"], "connection not reused")
    for name in ("success", "invalid_json"):
        check(runs["before"][name] == runs["after"][name], "ordinary response behavior changed")
    source = Path(preflight.__file__).read_bytes()
    print(json.dumps({
        "scope": "Exact production helper; real loopback HTTP only, no live GitHub or claim actions.",
        "python": platform.python_version(), "requests": requests.__version__, "urllib3": urllib3.__version__,
        "before_blob": blob_id(old), "after_blob": blob_id(source),
        "cases": runs, "compatibility": compatibility(preflight._get_json),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
