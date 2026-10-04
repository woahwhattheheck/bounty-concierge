#!/usr/bin/env python3
"""Observe retained HTTP-hook failures in the real Requests one-slot pool.

This is an opt-in loopback reproduction, not a provider probe or a test suite.
Pass the preserved baseline source and candidate source explicitly.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import platform
import sys
import threading
import time

import requests
from requests.adapters import HTTPAdapter


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        status = int(self.path.rsplit("/", 1)[-1]) if self.path.startswith("/status/") else 200
        body = json.dumps({"status": status}).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/json")
        self.send_header("Retry-After", "120")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, *args):
        # Closing the unread response can reset its HTTP/1.1 connection.
        pass


class Session(requests.Session):
    def __init__(self):
        super().__init__()
        self.trust_env = False
        self.close_calls = 0
        self.get_calls = 0

    def get(self, *args, **kwargs):
        self.get_calls += 1
        return super().get(*args, **kwargs)

    def close(self):
        self.close_calls += 1
        super().close()


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def observe(module, url, status):
    session = Session()
    adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1, pool_block=True)
    session.mount("http://", adapter)
    retained_hooks = []

    def hook(response, **kwargs):
        if response.status_code >= 400:
            try:
                response.raise_for_status()
            except requests.HTTPError as error:
                retained_hooks.append(error)
                raise

    session.hooks["response"].append(hook)
    retained = None
    try:
        module._get_json(session, f"{url}/status/{status}", headers={})
    except module.BountyAvailabilityError as error:
        retained = error
    assert retained is not None
    original = retained_hooks[0]
    # Read the already-used pool. Creating a second pool with a different TLS
    # key can evict the first one and would invalidate the leak observation.
    pool_key, = adapter.poolmanager.pools.keys()
    pool = adapter.poolmanager.pools[pool_key]
    slots = pool.pool.qsize()
    result = {}
    finished = threading.Event()

    def next_read():
        started = time.perf_counter()
        try:
            result["payload"] = module._get_json(session, f"{url}/healthy", headers={})
        except Exception as error:
            result["error"] = repr(error)
        finally:
            result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            finished.set()

    worker = threading.Thread(target=next_read, daemon=True)
    worker.start()
    completed_without_release = finished.wait(0.25)
    if not completed_without_release:
        original.response.close()
    assert finished.wait(2), "healthy loopback read did not finish after release"
    worker.join()
    row = {
        "status": status,
        "available_slots_after_failure": slots,
        "healthy_completed_before_manual_release": completed_without_release,
        "healthy_payload": result.get("payload"),
        "healthy_elapsed_ms": result["elapsed_ms"],
        "provider_cause_identity_preserved": retained.__cause__ is original,
        "provider_status_preserved": original.response.status_code,
        "retry_after_preserved": original.response.headers["Retry-After"],
        "caller_session_close_calls": session.close_calls,
        "http_get_calls": session.get_calls,
    }
    session.close()
    assert row["provider_cause_identity_preserved"]
    assert row["provider_status_preserved"] == status
    assert row["retry_after_preserved"] == "120"
    assert row["caller_session_close_calls"] == 0
    assert row["healthy_payload"] == {"status": 200}
    assert row["http_get_calls"] == 2
    return row


def cleanup_error(module):
    response = requests.Response()
    response.status_code = 429

    def close():
        raise RuntimeError("deliberate cleanup failure")

    response.close = close
    original = requests.HTTPError("original provider failure", response=response)

    class FailingTransport:
        def get(self, *args, **kwargs):
            raise original

    try:
        module._get_json(FailingTransport(), "http://loopback.invalid/status/429", headers={})
    except module.BountyAvailabilityError as error:
        assert error.__cause__ is original
        return {"cleanup_failure_keeps_original_cause": True}
    raise AssertionError("expected original provider failure")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    # Only these two exact modules and their unchanged config are imported.
    sys.path.insert(0, str(args.candidate.resolve().parents[1]))
    report = {
        "python": platform.python_version(),
        "requests": requests.__version__,
        "boundary": "loopback HTTP/1.1; caller-owned one-slot Requests pool",
        "observations": {},
    }
    with Server(("127.0.0.1", 0), Handler) as server:
        server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        server_thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        for label, path in [("baseline", args.baseline), ("candidate", args.candidate)]:
            data = path.read_bytes()
            module = load(path, f"availability_{label}")
            rows = [observe(module, url, status) for status in (429, 500)]
            expected_slots = 0 if label == "baseline" else 1
            assert all(row["available_slots_after_failure"] == expected_slots for row in rows)
            assert all(row["healthy_completed_before_manual_release"] == (label == "candidate") for row in rows)
            report["observations"][label] = {
                "git_blob": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "rows": rows,
                **cleanup_error(module),
            }
        server.shutdown()
        server_thread.join()
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
