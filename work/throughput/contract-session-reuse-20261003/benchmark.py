#!/usr/bin/env python3
"""Measure public contract capture/verify with real local HTTPS connections.

Run with the current package on PYTHONPATH and --baseline pointing to the
unchanged bounty_contract_hardening.py. No live GitHub endpoint is contacted.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import platform
import socket
import ssl
import statistics
import subprocess
import tempfile
from threading import Lock, Thread
import time
from urllib.parse import parse_qs, urlsplit

import requests

from concierge import bounty_contract as public
from concierge import bounty_contract_hardening as candidate
from concierge.bounty_contract_common import BountyContractError


REPO = "fixture/contract"
ISSUE_NUMBER = 1
STAMP = "2026-10-03T00:00:00Z"
ISSUE_PATH = f"/repos/{REPO}/issues/{ISSUE_NUMBER}"
ISSUE_URL = "https://api.github.com" + ISSUE_PATH
ISSUE = {
    "id": 1, "node_id": "I_fixture_1", "number": ISSUE_NUMBER,
    "html_url": f"https://github.com/{REPO}/issues/{ISSUE_NUMBER}",
    "url": ISSUE_URL,
    "repository_url": f"https://api.github.com/repos/{REPO}",
    "created_at": STAMP, "updated_at": STAMP,
    "title": "Fixture contract", "body": "Retained local benchmark terms.",
    "state": "open", "state_reason": None, "locked": False,
    "user": {"login": "fixture-maintainer", "type": "User"},
    "author_association": "OWNER", "labels": [], "assignees": [],
    "milestone": None, "comments": 101,
}
COMMENTS = [
    {
        "id": number, "node_id": f"IC_fixture_{number}",
        "user": {"login": f"fixture-user-{number}", "type": "User", "id": number,
                 "node_id": f"U_fixture_{number}"},
        "author_association": "MEMBER" if number % 20 == 1 else "CONTRIBUTOR",
        "created_at": STAMP, "updated_at": STAMP, "body": f"Fixture comment {number}.",
    }
    for number in range(1, 102)
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def blob_sha(path):
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


class Provider(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, tls):
        self.tls = tls
        self.lock = Lock()
        self.connections = 0
        self.requests = 0
        self.comment_pages = 0
        self.scenario = "stable"
        super().__init__(("127.0.0.1", 0), Handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        connection = self.tls.wrap_socket(connection, server_side=True)
        with self.lock:
            self.connections += 1
        return connection, address


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        with self.server.lock:
            self.server.requests += 1
            scenario = self.server.scenario
            if parsed.path == ISSUE_PATH:
                payload = deepcopy(ISSUE)
                if scenario == "incomplete":
                    payload["comments"] = 102
            elif parsed.path == ISSUE_PATH + "/comments":
                self.server.comment_pages += 1
                page = int(parse_qs(parsed.query)["page"][0])
                payload = deepcopy(COMMENTS[(page - 1) * 100:page * 100])
                if scenario == "same_second_edit" and self.server.comment_pages >= 3 and page == 1:
                    payload[0]["body"] += " Changed within the same timestamp."
            else:
                raise AssertionError("Unexpected provider path: " + self.path)
        status = 503 if scenario == "http_error" else 200
        body = canonical({"message": "Fixture unavailable"} if status == 503 else payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=15)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    spec = importlib.util.spec_from_file_location("concierge._contract_pool_baseline", args.baseline)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    implementations = {"before": baseline._read_stable_generation,
                       "after": candidate._read_stable_generation}
    session_class = requests.sessions.Session
    original_session_alias = requests.Session
    original_dns = socket.getaddrinfo
    original_reader = public._read_stable_generation
    observed_sessions = []

    with tempfile.TemporaryDirectory(prefix="contract-https-") as temporary:
        temporary = Path(temporary)
        cert, key = temporary / "cert.pem", temporary / "key.pem"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt",
             "ec_paramgen_curve:prime256v1", "-nodes", "-keyout", str(key),
             "-out", str(cert), "-days", "1", "-subj", "/CN=api.github.com",
             "-addext", "subjectAltName=DNS:api.github.com"],
            check=True, capture_output=True,
        )
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        server = Provider(tls)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()

        class ObservedSession(session_class):
            def __init__(self):
                super().__init__()
                self.trust_env = False  # No proxy, netrc, or credentials from the host.
                self.verify = str(cert)
                self.close_count = 0
                observed_sessions.append(self)

            def close(self):
                self.close_count += 1
                super().close()

        def local_dns(host, port, *positional, **keyword):
            if host not in ("api.github.com", b"api.github.com"):
                raise AssertionError("Benchmark attempted an unexpected network destination")
            return original_dns("127.0.0.1", server.server_port, *positional, **keyword)

        requests.sessions.Session = requests.Session = ObservedSession
        socket.getaddrinfo = local_dns

        def execute(which, operation="capture", scenario="stable", receipt=None, transport=None):
            public._read_stable_generation = implementations[which]
            with server.lock:
                server.scenario, server.comment_pages = scenario, 0
                previous_connections, previous_requests = server.connections, server.requests
            first_session = len(observed_sessions)
            options = {} if transport is None else {"session": transport}
            start = time.perf_counter_ns()
            try:
                if operation == "capture":
                    result = public.capture_contract(REPO, ISSUE_NUMBER, "fixture-only-token",
                                                     captured_at=STAMP, **options)
                else:
                    result = public.verify_contract(receipt, "fixture-only-token",
                                                    checked_at=STAMP, **options)
            except BountyContractError as error:
                result = {"error_type": type(error).__name__,
                          "reason_code": getattr(error, "reason_code", None)}
            elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
            sessions = observed_sessions[first_session:]
            metrics = {
                "elapsed_ms": elapsed_ms,
                "https_connections": server.connections - previous_connections,
                "http_requests": server.requests - previous_requests,
                "owned_sessions": len(sessions),
                "owned_sessions_closed": sum(session.close_count > 0 for session in sessions),
            }
            return result, metrics

        try:
            captures, verifications, checks = {}, {}, {}
            for which in implementations:
                captures[which], capture_metrics = execute(which)
                assert "receipt_sha256" in captures[which], captures[which]
                verifications[which], verify_metrics = execute(which, "verify", receipt=captures[which])
                assert verifications[which]["disposition"] == "UNCHANGED", verifications[which]
                checks[which] = {"capture": capture_metrics, "verify": verify_metrics}
            assert canonical(captures["before"]) == canonical(captures["after"])
            assert canonical(verifications["before"]) == canonical(verifications["after"])
            assert checks["before"]["capture"]["https_connections"] == 7
            assert checks["after"]["capture"]["https_connections"] == 1

            failures = {}
            for scenario, reason in (("same_second_edit", "LIVE_GENERATION_UNSTABLE"),
                                     ("incomplete", "LIVE_EVIDENCE_INCOMPLETE")):
                result, metrics = execute("after", "verify", scenario, captures["after"])
                assert result["disposition"] == "HOLD" and result["reason_codes"] == [reason], result
                assert metrics["owned_sessions"] == metrics["owned_sessions_closed"] == 1, metrics
                failures[scenario] = {"reason_codes": result["reason_codes"], **metrics}
            result, metrics = execute("after", scenario="http_error")
            assert result["error_type"] == "BountyContractError", result
            assert metrics["owned_sessions"] == metrics["owned_sessions_closed"] == 1, metrics
            failures["http_error"] = {**result, **metrics}

            supplied = ObservedSession()
            try:
                result, metrics = execute("after", transport=supplied)
                assert canonical(result) == canonical(captures["after"])
                assert supplied.close_count == 0 and metrics["owned_sessions"] == 0, metrics
                # A further real request demonstrates that ownership stayed with the caller.
                supplied.get(ISSUE_URL, timeout=15).raise_for_status()
                checks["injected_transport"] = {"closed_during_capture": supplied.close_count, **metrics}
            finally:
                supplied.close()

            timings = {which: {"capture": [], "verify": []} for which in implementations}
            for sample in range(args.samples):
                for which in (("before", "after") if sample % 2 == 0 else ("after", "before")):
                    for operation in ("capture", "verify"):
                        result, metrics = execute(which, operation, receipt=captures["after"])
                        expected = captures["after"] if operation == "capture" else verifications["after"]
                        assert canonical(result) == canonical(expected)
                        assert metrics["http_requests"] == 7
                        assert metrics["https_connections"] == (7 if which == "before" else 1)
                        assert metrics["owned_sessions_closed"] == metrics["owned_sessions"]
                        timings[which][operation].append(metrics["elapsed_ms"])
            result = {
                "method": "Public capture_contract and verify_contract; 101 fixture comments in two pages; real HTTP/1.1 over localhost TLS; alternating samples after initial executions. DNS is scoped to the fixture. Original package bootstrap and request/pagination/generation logic execute.",
                "limits": "Local provider fixture, not live GitHub/WAN latency, payment, or deployment performance. TLS credentials are generated locally and deleted.",
                "python": platform.python_version(), "requests_version": requests.__version__,
                "openssl": ssl.OPENSSL_VERSION,
                "baseline_source_blob": blob_sha(args.baseline),
                "candidate_source_blob": blob_sha(candidate.__file__),
                "fixture_sha256": hashlib.sha256(canonical({"issue": ISSUE, "comments": COMMENTS})).hexdigest(),
                "receipt_sha256": captures["after"]["receipt_sha256"],
                "public_capture_equal": True, "public_verification_equal": True,
                "initial_checks": checks, "failure_cleanup": failures,
                "samples_per_operation": args.samples, "timings_ms": timings,
                "median_ms": {which: {operation: statistics.median(values)
                                      for operation, values in operations.items()}
                              for which, operations in timings.items()},
            }
            print(json.dumps(result, indent=2, sort_keys=True))
        finally:
            public._read_stable_generation = original_reader
            requests.sessions.Session = session_class
            requests.Session = original_session_alias
            socket.getaddrinfo = original_dns
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    main()
