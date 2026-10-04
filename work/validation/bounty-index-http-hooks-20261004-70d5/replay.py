"""One offline production-path replay for bounty index response-hook handling."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types

import requests
from requests.adapters import BaseAdapter

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--baseline-root", required=True, type=Path,
                    help="Directory containing the pinned baseline concierge modules")
parser.add_argument("--candidate", required=True, type=Path,
                    help="Candidate bounty_index.py source file")
parser.add_argument("--output", type=Path, help="Optional path for the JSON receipt")
args = parser.parse_args()
package = types.ModuleType("concierge")
package.__path__ = [str(args.baseline_root.resolve() / "concierge")]
sys.modules["concierge"] = package
RealSession = requests.Session


class TrackedResponse(requests.Response):
    def __init__(self):
        super().__init__()
        self.close_count = 0

    def close(self):
        self.close_count += 1
        super().close()


class Adapter(BaseAdapter):
    def __init__(self, case):
        self.case, self.calls, self.responses = case, [], []

    def send(self, request, **kwargs):
        self.calls.append(request.url)
        position = len(self.calls)
        failure_position = 2 if self.case == "prior_rows_then_429" else 1
        failed = position == failure_position
        if failed and self.case == "response_less_http_error":
            raise requests.HTTPError("private request details must not enter the report")
        statuses = {
            "429": 429, "403_quota": 403, "401": 401, "403_permission": 403,
            "404": 404, "prior_rows_then_429": 429, "hook_rejects_200": 200,
        }
        response = TrackedResponse()
        response.status_code = statuses.get(self.case, 200) if failed else 200
        response.request, response.url = request, request.url
        if failed and self.case in ("429", "403_quota", "prior_rows_then_429"):
            response.headers.update({"Retry-After": "17", "X-RateLimit-Reset": "1791108000"})
        if response.status_code >= 400:
            payload = {"message": "Forbidden"}
        else:
            payload = [{"number": 1, "title": "25 RTC Python work", "body": "",
                        "html_url": "https://github.com/example/project/issues/1",
                        "labels": [{"name": "bounty"}]}]
        response._content = json.dumps(payload).encode()
        response._content_consumed = True
        self.responses.append(response)
        return response

    def close(self):
        pass


def run(module, case):
    adapter, session = Adapter(case), RealSession()
    session.trust_env = False
    session.mount("https://", adapter)

    def hook(response, **kwargs):
        if case == "hook_rejects_200" and len(adapter.calls) == 1:
            raise requests.HTTPError("hook rejected this response", response=response)
        response.raise_for_status()

    session.hooks["response"].append(hook)
    requests.Session = lambda: session
    try:
        report = module.fetch_bounties_report(
            repos=["example/one", "example/two", "example/three"],
            token="synthetic-replay-token", cache_dir=False,
        )
    finally:
        requests.Session = RealSession
    summary = {
        "get_attempts": len(adapter.calls),
        "statuses": [row["status"] for row in report["repositories"]],
        "http_statuses": [row["http_status"] for row in report["repositories"]],
        "rate_limited": report["rate_limited"],
        "retry_after_seconds": report["retry_after_seconds"],
        "rate_limit_reset_at": report["rate_limit_reset_at"],
        "retained_rows": report["total_count"],
        "response_close_counts": [response.close_count for response in adapter.responses],
    }
    quota = case in ("429", "403_quota", "prior_rows_then_429")
    count = 2 if case == "prior_rows_then_429" else 1 if quota or case == "401" else 3
    first_error = 1 if case == "prior_rows_then_429" else 0
    expected_status = "RATE_LIMITED" if quota else "TRANSPORT_ERROR" if case == "response_less_http_error" else "HTTP_ERROR"
    valid = (
        summary["get_attempts"] == count
        and summary["statuses"][first_error] == expected_status
        and summary["rate_limited"] == quota
        and all(value == 1 for value in summary["response_close_counts"])
        and (not quota or (
            summary["retry_after_seconds"] == 17
            and summary["rate_limit_reset_at"] == 1791108000
            and summary["statuses"][-1] == "NOT_ATTEMPTED_RATE_LIMIT"))
        and (case != "401" or summary["statuses"][1:] == ["NOT_ATTEMPTED_AUTH_ERROR"] * 2)
        and (case != "prior_rows_then_429" or summary["retained_rows"] == 1)
        and (quota or case == "401" or summary["retained_rows"] == 2)
        and "private request details" not in json.dumps(report)
    )
    summary["passed"] = valid
    return summary


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


results = {"runtime": {"python": sys.version.split()[0], "requests": requests.__version__},
           "provider_requests": 0, "transport": "real Requests Session + response hook + in-memory adapter",
           "scope": "production bounty_index module with its unchanged direct dependencies; package bootstrap excluded",
           "versions": {}}
cases = ("429", "403_quota", "401", "403_permission", "404", "response_less_http_error",
         "prior_rows_then_429", "hook_rejects_200")
for name, source in (
    ("baseline", args.baseline_root / "concierge" / "bounty_index.py"),
    ("candidate", args.candidate),
):
    data = source.read_bytes()
    module = load(source, "replay_" + name)
    outcomes = {case: run(module, case) for case in cases}
    results["versions"][name] = {
        "blob_sha": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest(),
        "passed": sum(case["passed"] for case in outcomes.values()), "cases": outcomes,
    }
rendered = json.dumps(results, indent=2) + "\n"
if args.output is not None:
    args.output.write_text(rendered)
print(rendered, end="")
if results["versions"]["candidate"]["passed"] != len(cases):
    raise SystemExit(1)
