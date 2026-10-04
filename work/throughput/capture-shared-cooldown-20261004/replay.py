#!/usr/bin/env python3
"""Offline wire-count replay: real Requests/SQLite, synthetic loopback provider."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from time import time
from unittest.mock import patch

import requests

from concierge.bounty_capture_batch import _BatchSession, collect_batch
from concierge.github_cooldown import GitHubCooldown, cooldown_deadline

TOKEN = "synthetic-replay-token"


def extend_in_process(args):
    path, deadline = args
    GitHubCooldown(path, TOKEN).extend(deadline)
    return deadline


class Handler(BaseHTTPRequestHandler):
    count = 0
    status = 429
    headers_to_send = {"Retry-After": "120"}
    body = {"message": "rate limit exceeded"}

    def do_GET(self):
        type(self).count += 1
        data = json.dumps(type(self).body).encode()
        self.send_response(type(self).status)
        for key, value in type(self).headers_to_send.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    observations = {}

    class LocalAdapter(requests.adapters.HTTPAdapter):
        def send(self, request, **kwargs):
            # The complete preflight calls its canonical GitHub URL. Only this
            # adapter's destination is synthetic; no external socket is opened.
            local = request.copy()
            local.url = endpoint + "/" + request.path_url.lstrip("/")
            return super().send(local, **kwargs)

    def session():
        value = requests.Session()
        value.trust_env = False
        value.mount("https://api.github.com/", LocalAdapter())
        return value

    try:
        with tempfile.TemporaryDirectory(prefix="capture-cooldown-") as temporary:
            root = Path(temporary)
            candidates = [{"repo": "example/project", "number": number} for number in range(1, 26)]
            for enabled in (False, True):
                Handler.count = 0
                reports = []
                for worker in range(25):
                    with session() as provider:
                        report = collect_batch(
                            candidates, root / f"batch-{enabled}-{worker}", token=TOKEN,
                            session=provider, cooldown_file=root / "batch.sqlite" if enabled else None,
                        )
                    assert report["remaining_count"] == 25 and report["captured_count"] == 0
                    assert not report["complete"] and report["stop_reason"] == "RATE_LIMITED"
                    remaining = json.loads((root / f"batch-{enabled}-{worker}" / "remaining.json").read_text())
                    assert len(remaining["candidates"]) == 25
                    assert TOKEN not in json.dumps(report)
                    reports.append(report)
                expected = 1 if enabled else 25
                assert Handler.count == expected
                assert sum(row["request_count"] for row in reports) == expected
                assert sum(row["shared_cooldown"]["deferred"] for row in reports) == (24 if enabled else 0)
                observations["shared" if enabled else "default_off"] = {
                    "capture_runs": 25, "wire_gets": Handler.count,
                    "reported_gets": sum(row["request_count"] for row in reports),
                    "retained_candidates_per_run": 25,
                    "deferred_runs": sum(row["shared_cooldown"]["deferred"] for row in reports),
                }

            # A different credential does not inherit the first token's row.
            Handler.count = 0
            with session() as provider:
                transport = _BatchSession(provider, 10, GitHubCooldown(root / "batch.sqlite", "other-test-token"))
                response = transport.get("https://api.github.com/user")
                response.close()
            assert Handler.count == 1
            observations["credential_isolation_gets"] = Handler.count

            # An expired persisted deadline permits an actual new read.
            expired = GitHubCooldown(root / "expired.sqlite", TOKEN)
            expired.extend(time() - 1)
            Handler.count = 0
            with session() as provider:
                transport = _BatchSession(provider, 10, expired)
                transport.get("https://api.github.com/user").close()
            assert Handler.count == 1 and not transport.shared_cooldown_deferred
            observations["expired_deadline_gets"] = Handler.count

            # Permission failure is not misclassified as a shared quota stop.
            Handler.count = 0
            Handler.status = 403
            Handler.headers_to_send = {}
            Handler.body = {"message": "Resource not accessible by integration"}
            ordinary = GitHubCooldown(root / "ordinary.sqlite", TOKEN)
            for _ in range(2):
                with session() as provider:
                    transport = _BatchSession(provider, 10, ordinary)
                    transport.get("https://api.github.com/user").close()
                    assert not transport.rate_limited
            assert Handler.count == 2 and ordinary.deadline() is None
            observations["ordinary_403_gets"] = Handler.count

            # A malformed opt-in file blocks dispatch and preserves unfinished rows.
            broken = root / "broken.sqlite"
            broken.write_bytes(b"not a database")
            Handler.count = 0
            with session() as provider:
                report = collect_batch(candidates, root / "broken-output", token=TOKEN,
                                       session=provider, cooldown_file=broken)
            assert Handler.count == 0 and report["stop_reason"] == "COOLDOWN_STATE_ERROR"
            assert report["remaining_count"] == 25 and report["shared_cooldown"]["state_error"]
            observations["invalid_store"] = {"wire_gets": 0, "remaining": 25, "stop": report["stop_reason"]}

            # Successful last-quota response is returned, not replaced by an error.
            Handler.count = 0
            Handler.status = 200
            Handler.headers_to_send = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time()) + 120)}
            Handler.body = {"login": "synthetic"}
            successful = GitHubCooldown(root / "successful.sqlite", TOKEN)
            with session() as provider:
                transport = _BatchSession(provider, 10, successful)
                response = transport.get("https://api.github.com/user")
                assert response.json() == Handler.body
                response.close()
                try:
                    transport.get("https://api.github.com/user")
                except requests.RequestException:
                    pass
                else:
                    raise AssertionError("second request should be deferred")
            assert Handler.count == 1 and successful.deadline() > time()
            observations["successful_last_quota_gets"] = Handler.count

            # Actual independent processes cannot shorten a longer observed stop.
            concurrent = root / "concurrent.sqlite"
            GitHubCooldown(concurrent, TOKEN).deadline()
            deadlines = [time() + offset for offset in (300, 120, 600, 60, 400, 180)]
            with ProcessPoolExecutor(max_workers=3) as pool:
                list(pool.map(extend_in_process, [(str(concurrent), value) for value in deadlines]))
            assert GitHubCooldown(concurrent, TOKEN).deadline() == max(deadlines)
            observations["atomic_extensions"] = {"writers": 6, "process_workers": 3, "maximum_retained": True}
            assert TOKEN.encode() not in concurrent.read_bytes()

            now = 1791117000.0
            future_date = datetime.fromtimestamp(now + 90, timezone.utc).isoformat().replace("+00:00", "Z")
            with patch("concierge.github_cooldown.time", return_value=now):
                base = dict(retry_seconds=None, retry_at=None, reset_at=None, primary_exhausted=False)
                assert cooldown_deadline(**base) == now + 60
                assert cooldown_deadline(**dict(base, retry_seconds=120)) == now + 120
                assert cooldown_deadline(**dict(base, retry_at=future_date)) == now + 90
                assert cooldown_deadline(**dict(base, retry_seconds=120, reset_at=int(now + 180), primary_exhausted=True)) == now + 180
                assert cooldown_deadline(**dict(base, retry_seconds=120, reset_at=int(now + 3600))) == now + 120
            observations["deadline_policy"] = "delta, HTTP-date-derived timestamp, primary-reset maximum, secondary reset isolation, 60s fallback"

            # Exercise the HTTP-date header through the real response parser.
            Handler.status = 429
            Handler.headers_to_send = {"Retry-After": format_datetime(datetime.fromtimestamp(time()+180, timezone.utc), usegmt=True)}
            date_store = GitHubCooldown(root / "date.sqlite", TOKEN)
            with session() as provider:
                transport = _BatchSession(provider, 10, date_store)
                transport.get("https://api.github.com/user").close()
            assert date_store.deadline() > time() + 175
            observations["http_date_response"] = "persisted"

    finally:
        server.shutdown()
        server.server_close()
        serving.join()
    observations["runtime"] = {"python": sys.version.split()[0], "requests": requests.__version__, "sqlite": sqlite3.sqlite_version}
    observations["boundary"] = "Real production capture/preflight/Requests + local HTTP and SQLite. No GitHub request, cloud-wide coordination, latency or paid completion claim."
    print(json.dumps(observations, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
