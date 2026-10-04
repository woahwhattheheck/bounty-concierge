"""Bounded production-module request-count replay; all provider responses are synthetic."""
from __future__ import annotations
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--baseline", type=Path, required=True)
parser.add_argument("--candidate", type=Path, required=True)
parser.add_argument("--output", type=Path)
args = parser.parse_args()

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._now = lambda: "2026-10-04T08:00:00+00:00"
    return module

old = load(args.baseline, "before")
new = load(args.candidate, "after")

class Response:
    def __init__(self, payload=None, status=200, headers=None):
        self.payload, self.status_code, self.headers = payload, status, headers or {}
        self.closed = False
    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return copy.deepcopy(self.payload)
    def close(self):
        self.closed = True

ROWS = []
for i in range(1, 27):
    ROWS.append({"id": f"00000000-0000-0000-0000-{i:012d}", "repositoryFullName": "example/demo",
                 "issueNumber": i, "htmlURL": f"https://github.com/example/demo/issues/{i}",
                 "title": f"Synthetic task {i}", "issueState": "open", "assignmentType": "COMPETITION",
                 "claimed": False, "retracted": False, "solved": False, "isFrozen": False,
                 "totalAmount": "50.00" if i <= 17 else "10.00", "language": "Python"})
DETAILS = {row["id"]: {**copy.deepcopy(row), "pledges": [{"retracted": False, "amount": row["totalAmount"],
                          "paymentStatus": "PAID", "isPaid": False}], "claims": []} for row in ROWS}

class Session:
    def __init__(self, overrides=None):
        self.calls, self.responses = [], []
        self.overrides = overrides or {}
    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if url in self.overrides:
            value = self.overrides[url]
            if isinstance(value, Exception):
                raise value
            response = value
        elif url == old.API:
            assert params == {"page": 1, "limit": 100}
            response = Response({"data": ROWS, "hasNextPage": False})
        else:
            assert params is None and url.startswith(old.API + "/")
            response = Response(DETAILS[url.rsplit("/", 1)[-1]])
        self.responses.append(response)
        return response
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False

checks = []
results = {}
def record(name):
    checks.append(name)
def guarded(snapshot, expected_exception=ValueError, **kwargs):
    session = Session()
    try:
        new.resume_catalog(snapshot, session=session, **kwargs)
    except expected_exception:
        assert not session.calls
        return
    raise AssertionError("expected rejection before provider reads")

# The new collector must remain identical to the baseline under all reused I/O scenarios.
for name, overrides in [
    ("complete", {}),
    ("rate_limit", {old.API + "/" + ROWS[12]["id"]: Response(status=429, headers={"Retry-After": "60"})}),
    ("removed", {old.API + "/" + ROWS[2]["id"]: Response(status=404)}),
    ("malformed", {old.API + "/" + ROWS[3]["id"]: Response(ValueError("bad JSON"))}),
]:
    a, b = Session(overrides), Session(copy.deepcopy(overrides))
    before, after = old.fetch_catalog(session=a), new.fetch_catalog(session=b)
    assert before == after, name
    assert a.calls == b.calls, name
record("collect output and call sequence unchanged across four original paths")

initial_session = Session({old.API + "/" + ROWS[12]["id"]: Response(status=429, headers={"Retry-After": "60"})})
initial = old.fetch_catalog(session=initial_session)
assert len(initial_session.calls) == 14 and initial["details_fetched"] == 12
assert initial["catalog_complete"] and not initial["complete"] and initial["rate_limited"]
frozen = copy.deepcopy(initial)
# No transport is opened for validation failure or a no-op.
opened = []
def factory():
    opened.append(True)
    return Session()
new.requests.Session = factory

guarded(initial)
retrying_server = copy.deepcopy(initial)
retrying_server["rate_limited"] = False
guarded(retrying_server)
assert not opened
record("recorded cooldown refuses reads")
new._now = lambda: "2026-10-04T08:01:01+00:00"
session = Session()
resumed = new.resume_catalog(initial, session=session)
full_session = Session()
full = old.fetch_catalog(session=full_session)
assert len(full_session.calls) == 18 and len(session.calls) == 5
assert resumed["complete"] and resumed["shortlist"]["source_complete"]
assert resumed["listings"] == full["listings"]
assert resumed["listings"][:12] == frozen["listings"][:12]
assert initial == frozen
assert resumed["started_at"] == initial["started_at"]
assert resumed["catalog_observed_through"] == initial["completed_at"]
assert resumed["completed_at"] == "2026-10-04T08:01:01+00:00"
assert resumed["resume"]["source_completed_at"] == initial["completed_at"]
assert resumed["resume"]["source_error_count"] == 1
assert resumed["resume"]["source_sha256"] == hashlib.sha256(json.dumps(initial, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
assert resumed["requests_made"] == 19 and resumed["details_fetched"] == 17
assert all(url != old.API for url, *_ in session.calls)
assert all(response.closed for response in session.responses)
record("429 recovery: 18 to 5 GETs, identical funding rows, immutable source and original catalog age")
results.update(initial_requests=14, fresh_restart_requests=18, resumed_requests=5,
               restart_total_requests=32, resume_total_requests=19,
               recovery_request_reduction_percent=round(100*(18-5)/18, 2),
               whole_capture_request_reduction_percent=round(100*(32-19)/32, 2),
               synthetic_listings=26, selected_details=17, completed_details_reused=12)

limited_session = Session()
limited = new.resume_catalog(initial, max_details=2, session=limited_session)
assert len(limited_session.calls) == 2 and not limited["complete"]
last_session = Session()
last = new.resume_catalog(limited, session=last_session)
assert len(last_session.calls) == 3 and last["listings"] == full["listings"]
assert last["catalog_observed_through"] == initial["completed_at"]
assert last["requests_made"] == 19
record("bounded 2+3 request continuation preserves original catalog age and totals")

new._now = lambda: "2026-10-04T09:00:00+00:00"
noop = new.resume_catalog(resumed)
assert not opened
assert noop["completed_at"] == resumed["completed_at"]
assert noop["requests_made"] == resumed["requests_made"]
assert noop["listings"] == resumed["listings"]
record("completed input opens no session and does not advance observation dates")
zero = new.resume_catalog(initial, max_details=0)
assert not opened and not zero["complete"]
assert zero["completed_at"] == initial["completed_at"] and zero["rate_limited"]
assert zero["retry_after_seconds"] == initial["retry_after_seconds"]
assert zero["resume"]["requests_made"] == 0
invalid_json = copy.deepcopy(initial)
invalid_json["extra"] = float("nan")
guarded(invalid_json)
record("zero budget preserves age and cooldown; nonfinite source rejects before reads")

for key, value in [("catalog_complete",False), ("listing_count",999), ("source_url","https://example.invalid"),
                   ("completed_at","2026-10-04T08:00:00"), ("completed_at","2099-10-04T08:00:00+00:00"), ("requests_made",True)]:
    broken = copy.deepcopy(initial); broken[key] = value; guarded(broken)
for change in ("url", "duplicate", "money", "floor"):
    broken = copy.deepcopy(initial)
    if change == "url": broken["listings"][-1]["source_url"] = "https://example.invalid/private"
    if change == "duplicate": broken["listings"][-1] = copy.deepcopy(broken["listings"][0])
    if change == "money": broken["listings"][0]["reported_funded_usd"] = "999.00"
    if change == "floor": broken["shortlist"]["minimum_funded_usd"] = "999.00"
    guarded(broken)
assert not opened
record("malformed late rows, URLs, timestamps, counts, duplicate identities and scope reject before reads")

for status, expected_calls in [(404,5),(410,5),(429,1),(500,1)]:
    first = old.API + "/" + ROWS[12]["id"]
    session = Session({first: Response(status=status, headers={"Retry-After":"30"} if status == 429 else {})})
    r = new.resume_catalog(initial, session=session)
    assert len(session.calls) == expected_calls and not r["complete"]
    assert r["rate_limited"] == (status == 429)
record("404/410 continue; 429/500 stop, retaining partial rows and quota evidence")

# CLI uses the real main() and serializer, with only transport substituted.
with tempfile.TemporaryDirectory() as tmp:
    snapshot_path = Path(tmp)/"partial.json"
    snapshot_path.write_text(json.dumps(initial))
    live_sessions=[]
    def tracked_factory():
        item=Session(); live_sessions.append(item); return item
    new.requests.Session=tracked_factory
    for budget, expected_code in [(2,2),(50,0)]:
        out, err=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            code=new.main(["resume",str(snapshot_path),"--max-details",str(budget)])
        result=json.loads(out.getvalue())
        assert code == expected_code and len(live_sessions[-1].calls) == min(budget,5)
        assert result["resume"]["catalog_refreshed"] is False
        assert ("PARTIAL:" in err.getvalue()) == (expected_code==2)
    snapshot_path.write_text(json.dumps(resumed))
    out,err=io.StringIO(),io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        assert new.main(["targets",str(snapshot_path)]) == 0
    assert json.loads(out.getvalue()) == {"candidates":full["shortlist"]["targets"]}
    assert "catalog_refreshed=false" in err.getvalue()
    assert "catalog_observed_through=2026-10-04T08:00:00+00:00" in err.getvalue()
    previous_sessions = len(live_sessions)
    snapshot_path.write_text(" " * (4 * 1024 * 1024 + 1))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert new.main(["resume",str(snapshot_path)]) == 2
    assert len(live_sessions) == previous_sessions
record("real resume/targets CLI: partial exit2, complete exit0, age warning, oversized source no session")

# Preserve the report's explicit funding basis rather than silently changing it.
promised = copy.deepcopy(initial)
promised["shortlist"] = old.select_targets(promised,promised["minimum_total_usd"],include_promised=True)
r = new.resume_catalog(promised,session=Session())
assert r["shortlist"]["reward_basis"] == "reported_funded_plus_promised"
record("explicit promised-reward scope preserved")

results["checks"]=checks
results["checks_passed"]=len(checks)
results["network_requests"]=0
results["boundary"]="Entire production module and real CLI; controlled synthetic provider responses. Not a live-provider, fleet-latency, bounty availability, award or payment measurement."
for label in ("baseline","candidate"):
    b=getattr(args, label).read_bytes()
    results[label+"_git_blob_sha"]=hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest()
if args.output:
    args.output.write_text(json.dumps(results,indent=2)+"\n")
print(json.dumps(results,indent=2))
