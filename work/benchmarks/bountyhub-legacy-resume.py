"""Exercise legacy catalog recovery; no network or provider requests are made."""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha1, sha256
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._now = lambda: "2026-10-04T13:00:00+00:00"
    return module


def blob(path: Path) -> str:
    data = path.read_bytes()
    return sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    before, after = load(args.baseline, "before"), load(args.candidate, "after")
    source = json.loads(args.snapshot.read_text())
    frozen = deepcopy(source)
    assert "page_size" not in source
    checks = []
    with patch.object(after.requests, "Session", side_effect=AssertionError("unexpected session")):
        try:
            before.resume_catalog(source)
        except KeyError as exc:
            assert exc.args == ("page_size",)
        else:
            raise AssertionError("baseline did not reproduce legacy failure")
        resumed = after.resume_catalog(source)
        assert resumed["complete"] and resumed["resume"]["requests_made"] == 0
        assert resumed["listings"] == source["listings"]
        assert resumed["shortlist"]["targets"] == source["shortlist"]["targets"]
        assert "page_size" not in resumed
        assert resumed["started_at"] == source["started_at"]
        assert resumed["completed_at"] == source["completed_at"]
        assert resumed["catalog_observed_through"] == source["completed_at"]
        expected_digest = sha256(json.dumps(source, sort_keys=True, separators=(",", ":"),
                                              allow_nan=False).encode()).hexdigest()
        assert resumed["resume"]["source_sha256"] == expected_digest
        assert after.resume_catalog(resumed)["completed_at"] == source["completed_at"]
        checks.append("real retained catalog: baseline KeyError; recovery and second no-op use zero sessions")
        modern = {**deepcopy(source), "page_size": 100}
        assert before.resume_catalog(modern) == after.resume_catalog(modern)
        for invalid in (None, True, 0, 101, "100"):
            try:
                after.resume_catalog({**source, "page_size": invalid})
            except ValueError:
                pass
            else:
                raise AssertionError(f"invalid page_size accepted: {invalid!r}")
        checks.append("present page_size remains strict; modern complete output unchanged")
        pending = deepcopy(source)
        row = next(row for row in pending["listings"] if row["funding_status"] == "COMPLETE")
        row["funding_status"] = "DETAIL_LIMIT"
        pending.update(complete=False, details_complete=False)
        zero = after.resume_catalog(pending, max_details=0)
        assert not zero["complete"] and zero["completed_at"] == source["completed_at"]
        assert zero["resume"]["requests_made"] == 0 and "page_size" not in zero
        checks.append("legacy partial zero budget retains its old observation and opens no session")
        cooldown = {**deepcopy(pending), "completed_at": "2026-10-04T13:00:00+00:00",
                    "rate_limited": True, "retry_after_seconds": 60}
        try:
            after.resume_catalog(cooldown)
        except ValueError as exc:
            assert "cooldown" in str(exc)
        else:
            raise AssertionError("cooldown bypassed")
        for malformed in ({**source, "catalog_complete": False},
                          {**source, "listing_count": source["listing_count"] + 1}):
            try:
                after.resume_catalog(malformed)
            except ValueError:
                pass
            else:
                raise AssertionError("malformed retained catalog accepted")
        checks.append("legacy cooldown and retained coverage/identity checks still reject before I/O")

    # Controlled synthetic detail response, not a new observation of the real listing.
    row = next(row for row in pending["listings"] if row["funding_status"] == "DETAIL_LIMIT")
    detail = {"id": row["listing_id"], "repositoryFullName": row["repo"], "issueNumber": row["number"],
              "htmlURL": row["issue_url"], "title": row["title"], "issueState": row["issue_state"],
              "assignmentType": row["assignment_type"], "claimed": False, "retracted": False,
              "solved": False, "isFrozen": False, "totalAmount": row["advertised_total_usd"],
              "pledges": [{"retracted": False, "amount": row["advertised_total_usd"],
                           "paymentStatus": "PROMISED", "isPaid": False}], "claims": []}
    class Response:
        status_code, headers, closed = 200, {}, False
        def json(self): return deepcopy(detail)
        def close(self): self.closed = True
    class Session:
        def __init__(self): self.calls, self.responses = [], []
        def get(self, url, **kwargs):
            assert url == row["source_url"]
            self.calls.append((url, kwargs))
            response = Response()
            self.responses.append(response)
            return response
    left, right = Session(), Session()
    expected = before.resume_catalog({**pending, "page_size": 100}, session=left)
    actual = after.resume_catalog(pending, session=right)
    assert len(right.calls) == 1 and right.calls == left.calls
    assert actual["listings"] == expected["listings"] and actual["complete"]
    assert "page_size" not in actual and all(response.closed for response in right.responses)
    assert actual["catalog_observed_through"] == source["completed_at"]
    assert actual["requests_made"] == source["requests_made"] + 1
    assert actual["resume"]["requests_made"] == 1 and source == frozen
    checks.append("controlled pending detail: one budgeted read, identical normalization, response closed, source unchanged")
    print(json.dumps({"baseline_blob": blob(args.baseline), "candidate_blob": blob(args.candidate),
                      "snapshot_sha256": sha256(args.snapshot.read_bytes()).hexdigest(),
                      "retained_listings": len(source["listings"]), "retained_original_requests": source["requests_made"],
                      "live_provider_requests": 0, "complete_recovery_session_opens": 0,
                      "controlled_partial_detail_reads": len(right.calls), "checks": checks,
                      "measurement_boundary": "Actual production modules; retained no-op plus controlled detail transport. No new availability or fleet latency measurement."}, indent=2))


if __name__ == "__main__":
    main()
