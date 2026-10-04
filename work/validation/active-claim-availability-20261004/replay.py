"""Offline Requests replay for the real active-claim portfolio readers.

Only HTTP responses and the documented verifier-clock seam are controlled.
The qualification, availability, and portfolio implementations run unchanged.
"""
from __future__ import annotations

import argparse
import collections
from datetime import datetime, timezone
import importlib.util
import inspect
import json
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.parse import urlparse

import requests

POLICY = {
    "version": "replay-v1",
    "max_active_claims_total": 500,
    "max_active_claims_per_worker": 500,
    "max_active_claims_per_sponsor": 500,
    "sponsor_overrides": {},
    "max_claim_age_seconds": 3600,
}


def candidate(listing: str | None, *, repo: str = "replay/project", number: int = 17) -> dict:
    item = {
        "repo": repo, "number": number, "sponsor_key": "synthetic-replay",
        "worker_id": "offline-worker", "reward_currency": "USD", "reward_minor": 10000,
    }
    if listing is not None:
        item["listing_url"] = listing
    return item


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("acp_replayed", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Replay:
    def __init__(self, *, failure_phase: str | None = None, failure_number: int = 17, failure_status: int = 429, failure_listing: str | None = None):
        self.calls = []
        self.unknown_routes = []
        self.failure_phase = failure_phase
        self.failure_number = failure_number
        self.failure_status = failure_status
        self.failure_listing = failure_listing

    def send(self, adapter, request, **kwargs):
        parsed = urlparse(request.url)
        stack = inspect.stack()
        phase = "availability" if any(frame.function == "_inspect_live_availability" for frame in stack) else "qualification"
        listing = next((frame.frame.f_locals.get("listing_url") for frame in stack if frame.function == "_qualify_live_authority"), None)
        self.calls.append({"phase": phase, "method": request.method, "url": request.url})
        status = 200
        headers = {}
        if parsed.hostname != "api.github.com" or request.method != "GET":
            self.unknown_routes.append(request.url)
            raise AssertionError("unexpected replay route")
        parts = parsed.path.strip("/").split("/")
        if parsed.path == "/user":
            payload = {"login": "offline-worker", "id": 293}
        elif parsed.path == "/search/issues":
            payload = {"items": [], "total_count": 0, "incomplete_results": False}
        elif len(parts) == 6 and parts[3] == "issues" and parts[5] in {"comments", "timeline"}:
            payload = []
        elif len(parts) == 5 and parts[0] == "repos" and parts[3] == "issues":
            repo = "/".join(parts[1:3])
            number = int(parts[4])
            payload = {
                "id": 100000 + number, "node_id": f"ISSUE_{number}", "number": number,
                "url": f"https://api.github.com/repos/{repo}/issues/{number}",
                "html_url": f"https://github.com/{repo}/issues/{number}",
                "state": "open", "state_reason": None,
                "title": "Synthetic replay: $100 bounty for a feature",
                "body": "Synthetic offline fixture. Reward: $100. Implement the requested feature.",
                "labels": [{"name": "bounty"}], "comments": 0, "assignees": [],
                "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-04T08:00:00Z",
                "closed_at": None, "author_association": "OWNER",
                "user": {"login": "replay-owner", "type": "User"}, "locked": False,
            }
            if phase == self.failure_phase and number == self.failure_number and (self.failure_listing is None or listing == self.failure_listing):
                status = self.failure_status
                payload = {"message": "rate limit exceeded" if status == 429 else "permission denied"}
                if status == 429:
                    headers = {"Retry-After": "60"}
        else:
            self.unknown_routes.append(request.url)
            raise AssertionError("unexpected replay route")
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(payload).encode("utf-8")
        response.headers.update({"Content-Type": "application/json", **headers})
        response.url = request.url
        response.request = request
        return response


def run(module, rows: list[dict], *, max_pages=2, **kwargs):
    replay = Replay(**kwargs)
    def send(adapter, request, **options):
        return replay.send(adapter, request, **options)
    with patch.object(requests.adapters.HTTPAdapter, "send", send), patch.object(
        module, "_utc_now", return_value=datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
    ):
        receipt = module.compile_live_active_claim_portfolio(rows, [], POLICY, max_pages=max_pages)
    if replay.unknown_routes:
        raise AssertionError(replay.unknown_routes)
    phases = dict(collections.Counter(call["phase"] for call in replay.calls))
    return {"prepared_gets": len(replay.calls), "phases": phases, "receipt": receipt, "requests": replay.calls}


def cases(module):
    a = "https://bountyhub.dev/bounties/replay-one"
    b = "https://bountyhub.dev/bounties/replay-two"
    rows = [candidate(a), candidate(b)]
    return {
        "same_issue_two_listings": run(module, rows),
        "same_issue_same_listing": run(module, [candidate(a), candidate(a)]),
        "same_issue_case_alias": run(module, [candidate(a, repo="REPLAY/Project"), candidate(b)]),
        "different_issue": run(module, [candidate(a), candidate(b, number=18)]),
        "different_repo": run(module, [candidate(a), candidate(b, repo="replay/another")]),
        "max_pages_one": run(module, rows, max_pages=1),
        "availability_permission403": run(module, rows, failure_phase="availability", failure_status=403),
        "qualification_rate429": run(module, rows, failure_phase="qualification"),
        "availability_rate429": run(module, rows, failure_phase="availability"),
        "qualification_rate429_after_cached_availability": run(module, rows, failure_phase="qualification", failure_listing=b),
        "fresh_second_compilation": run(module, rows),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dependencies", type=Path, default=Path(__file__).resolve().parents[3])
    args = parser.parse_args()
    sys.path.insert(0, str(args.dependencies))
    result = cases(load_module(args.source))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({name: {"prepared_gets": data["prepared_gets"], "phases": data["phases"], "dispositions": [row["disposition"] for row in data["receipt"]["results"]]} for name, data in result.items()}, indent=2))


if __name__ == "__main__":
    main()
