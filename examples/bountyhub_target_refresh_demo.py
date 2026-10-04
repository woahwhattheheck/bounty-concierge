#!/usr/bin/env python3
"""Exercise full catalog and targeted refresh through native Requests on loopback.

No provider requests, credentials, claims or payments. The only transport change
maps the canonical BountyHub GET destination to this process's HTTP server.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import io
import json
from pathlib import Path
import platform
import sys
import tempfile
from threading import Thread
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
from concierge import bountyhub_catalog as catalog
from concierge import bountyhub_refresh as refresh


def listing(index: int) -> dict:
    return {
        "id": f"00000000-0000-0000-0000-{index:012d}",
        "repositoryFullName": "fixture/project", "issueNumber": index,
        "htmlURL": f"https://github.com/fixture/project/issues/{index}",
        "title": f"Fixture {index}", "issueState": "open", "assignmentType": "NON_EXCLUSIVE",
        "assignee": None, "claimed": False, "retracted": False, "solved": False,
        "isFrozen": False, "deletedAt": None, "totalAmount": "50.00",
        "pledges": [{"retracted": False, "deletedAt": None, "amount": "50.00",
                     "paymentStatus": "PAID", "isPaid": False}],
        "claims": [], "private_extra": "NOT_FOR_EXPORT",
    }


def run() -> dict:
    data = [listing(index) for index in range(1, 51)]
    by_id = {row["id"]: row for row in data}
    responses: dict[str, tuple[int, object, dict]] = {}
    wire: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            wire.append(self.path)
            parsed = urlsplit(self.path)
            ident = parsed.path.rsplit("/", 1)[-1]
            if parsed.path == "/api/bounties":
                query = parse_qs(parsed.query)
                page, limit = int(query["page"][0]), int(query["limit"][0])
                payload = {"data": data[(page - 1) * limit:page * limit],
                           "hasNextPage": page * limit < len(data)}
                status, headers = 200, {}
            else:
                status, payload, headers = responses.get(ident, (200, by_id[ident], {}))
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    native_session = requests.Session

    class LoopbackSession(native_session):
        def __init__(self):
            super().__init__()
            self.trust_env = False
            self.closed = False

        def get(self, url, **kwargs):
            assert url == catalog.API or url.startswith(catalog.API + "/")
            url = f"http://127.0.0.1:{server.server_port}/api/bounties" + url[len(catalog.API):]
            return super().get(url, **kwargs)

        def close(self):
            self.closed = True
            super().close()

    client = LoopbackSession()
    results = {}
    try:
        snapshot = catalog.fetch_catalog(max_pages=5, max_details=50, page_size=10, session=client)
        assert snapshot["complete"] and snapshot["requests_made"] == len(wire) == 55
        first, second, third = [row["id"] for row in data[:3]]
        # The persisted report is immutable; later provider details have changed.
        digest_before = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        data[0].update(issueState="closed")
        data[1]["totalAmount"] = data[1]["pledges"][0]["amount"] = "80.00"
        data[1]["claims"] = [{"deletedAt": None, "isOpen": True, "rejectedAt": None}] * 2

        wire.clear()
        repeated = catalog.fetch_catalog(max_pages=5, max_details=50, page_size=10, session=client)
        # A newly closed listing is skipped during full collection (49 details).
        repeated_count = len(wire)
        assert repeated_count == repeated["requests_made"] == 54
        wire.clear()
        refreshed = refresh.refresh_listings(snapshot, [first, second, second], session=client)
        assert len(wire) == refreshed["requests_made"] == 2
        assert refreshed["candidates"] == [{"repo": "fixture/project", "number": 2}]
        assert refreshed["records"][0]["listing"]["issue_state"] == "closed"
        row = refreshed["records"][1]["listing"]
        assert row["reported_funded_usd"] == "80.00" and row["open_claim_count"] == 2
        assert row == repeated["listings"][1]
        assert refreshed["complete"] and refreshed["catalog_refreshed"] is False
        assert refreshed["catalog_observed_through"] == snapshot["completed_at"]
        assert refreshed["completed_at"] >= snapshot["completed_at"]
        assert "NOT_FOR_EXPORT" not in json.dumps(refreshed)
        assert hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest() == digest_before
        assert not client.closed
        results["fresh_changes_and_dedup"] = {
            "full_recollection_wire_gets": repeated_count, "targeted_wire_gets": len(wire),
            "requested_ids": 3, "distinct_ids": 2, "candidate_issue_numbers": [2],
            "fresh_funded_usd": row["reported_funded_usd"], "fresh_open_claims": row["open_claim_count"],
            "catalog_timestamp_preserved": True, "source_unchanged": True,
        }
        wire.clear()
        resumed = catalog.resume_catalog(snapshot, session=client)
        assert not wire and resumed["listings"][1]["reported_funded_usd"] == "50.00"
        results["resume_is_not_refresh"] = {"wire_gets": 0, "retained_funded_usd": "50.00"}

        wire.clear()
        responses[first] = (429, {"message": "limited"}, {"Retry-After": "120"})
        limited = refresh.refresh_listings(snapshot, [first, second], session=client)
        assert len(wire) == 1 and limited["rate_limited"] and limited["retry_after_seconds"] == 120
        assert not limited["complete"] and not limited["candidates"]
        assert [item["status"] for item in limited["records"]] == ["READ_FAILED", "NOT_ATTEMPTED"]
        assert all(item["listing"] is None for item in limited["records"])
        try:
            refresh.refresh_listings(snapshot, [second], previous_refresh=limited, session=client)
            raise AssertionError("cooldown admitted")
        except ValueError as exc:
            assert "cooldown" in str(exc)
        assert len(wire) == 1
        results["shared_cooldown"] = {"wire_gets": 1, "early_repeat_wire_gets": 0,
                                      "retry_after_seconds": 120, "stale_fallbacks": 0}

        wire.clear()
        responses[first] = (404, {}, {})
        malformed = deepcopy(data[1])
        malformed["issueNumber"] = 999
        malformed["htmlURL"] = "https://github.com/fixture/project/issues/999"
        responses[second] = (200, malformed, {})
        partial = refresh.refresh_listings(snapshot, [first, second, third], session=client)
        assert len(wire) == 3 and not partial["complete"]
        assert [item["status"] for item in partial["records"]] == ["READ_FAILED", "INVALID_DETAIL", "COMPLETE"]
        assert partial["candidates"] == [{"repo": "fixture/project", "number": 3}]
        results["independent_rows"] = {"wire_gets": 3, "statuses": [r["status"] for r in partial["records"]],
                                       "candidate_issue_numbers": [3], "complete": False}

        wire.clear()
        responses.clear()
        legacy = deepcopy(snapshot)
        del legacy["page_size"]
        legacy_digest = hashlib.sha256(json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        legacy_result = refresh.refresh_listings(legacy, [second], session=client)
        assert len(wire) == 1 and legacy_result["complete"]
        assert legacy_result["source_page_size"] is None and "page_size" not in legacy
        assert legacy_result["source_sha256"] == legacy_digest
        results["early_v1_compatibility"] = {"wire_gets": 1, "past_page_size": None,
                                              "original_digest_preserved": True}

        wire.clear()
        rejected = 0
        for ids in ([], [first] * 101, ["unknown"], [listing(999)["id"]]):
            try:
                refresh.refresh_listings(snapshot, ids, session=client)
                raise AssertionError("invalid selection admitted")
            except ValueError:
                rejected += 1
        assert rejected == 4 and not wire
        results["input_admission"] = {"rejected_selections": rejected, "wire_gets": 0}

        responses.clear()
        # Prior payout and promises stay distinct; a successful read is not eligibility.
        data[2]["pledges"][0]["isPaid"] = True
        wire.clear()
        paid = refresh.refresh_listings(snapshot, [third], session=client)
        assert not paid["complete"] and not paid["candidates"] and paid["unresolved_funding_count"] == 1
        data[2]["pledges"][0].update(isPaid=False, paymentStatus="PROMISED")
        promised = refresh.refresh_listings(snapshot, [third], session=client)
        assert promised["complete"] and not promised["candidates"]
        expanded = deepcopy(snapshot)
        expanded["shortlist"] = catalog.select_targets(expanded, "25.00", include_promised=True)
        promised = refresh.refresh_listings(expanded, [third], session=client)
        assert promised["complete"] and len(promised["candidates"]) == 1
        assert promised["reward_basis"] == "reported_funded_plus_promised"
        results["existing_economic_selection"] = {"wire_gets": len(wire), "payout_excluded": True,
                                                   "promised_requires_explicit_scope": True}

        wire.clear()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "catalog.json"
            path.write_text(json.dumps(snapshot))
            requests.Session = LoopbackSession
            try:
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    status = catalog.main(["refresh", str(path), "--listing-id", second])
                actual = json.loads(out.getvalue())
                assert status == 0 and actual["schema"] == refresh.SCHEMA and not err.getvalue()
                assert len(wire) == 1 and actual["records"][0]["listing"]["reported_funded_usd"] == "80.00"
            finally:
                requests.Session = native_session
        results["catalog_cli"] = {"wire_gets": len(wire), "exit_status": status, "schema": actual["schema"]}
        assert not client.closed
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    source = Path(refresh.__file__)
    return {
        "schema": "bountyhub-target-refresh-demo/v1", "result": "PASS",
        "python": platform.python_version(), "requests": requests.__version__,
        "platform": platform.platform(), "baseline_commit": "4f5bc01a9b6d6b91d5937d9743ed3608f9003ebc",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "loaded_module_path": str(source),
        "catalog_sha256": hashlib.sha256(Path(catalog.__file__).read_bytes()).hexdigest(),
        "scope": "Full imported package + native Requests + HTTP/1.1 loopback. No live provider or fleet latency measurement.",
        "checks": results,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
