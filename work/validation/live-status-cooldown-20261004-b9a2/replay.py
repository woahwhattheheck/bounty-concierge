"""Controlled-response replay of the complete live-status module; no network.

Package bootstrap is excluded. Only the ambient-token config is supplied as an
empty-token module. Each source file is otherwise compiled and executed whole.
The private HTTP seam returns actual requests.Response objects (or the same
requests.ConnectionError), and the clock is fixed for byte-for-byte comparison.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
import types
from datetime import datetime, timezone

import requests

ROOT = Path(__file__).parent
NOW = datetime(2026, 10, 4, 9, 30, 0, 250000, tzinfo=timezone.utc)
URL = "https://api.github.com/repos/example/project/issues/1"
WEB = "https://github.com/example/project/issues/1"

def no_network(*args, **kwargs):
    raise AssertionError("live network is outside this replay")

socket.socket.connect = no_network
package = types.ModuleType("concierge")
package.__path__ = []
config = types.ModuleType("concierge.config")
config.GITHUB_TOKEN = ""
sys.modules["concierge"] = package
sys.modules["concierge.config"] = config

def load(name, path):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    module._now_utc = lambda: NOW
    return module

before = load("before", ROOT / "before.py")
after = load("after", ROOT / "after.py")
cases = [
    ("429 seconds", 429, {"Retry-After": "90"}, URL, 90, True),
    ("429 HTTP date rounds up", 429, {"retry-after": "Sun, 04 Oct 2026 09:31:00 GMT"}, URL, 60, True),
    ("403 later primary reset", 403, {"Retry-After": "30", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(NOW.timestamp()) + 90)}, URL, 90, True),
    ("403 later retry after", 403, {"Retry-After": "120", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(NOW.timestamp()) + 30)}, URL, 120, True),
    ("permission 403", 403, {"X-RateLimit-Remaining": "20", "X-RateLimit-Reset": str(int(NOW.timestamp()) + 90)}, URL, None, False),
    ("429 malformed delay", 429, {"Retry-After": "NaN", "X-RateLimit-Reset": "bogus"}, URL, None, True),
    ("429 unknown delay", 429, {}, URL, None, True),
    ("429 oversized delay", 429, {"Retry-After": "9" * 129}, URL, None, True),
    ("429 representational overflow", 429, {"Retry-After": "999999999999"}, URL, 999999999999, True),
    ("open issue", 200, {}, URL, None, False),
    ("not found", 404, {}, URL, None, False),
    ("gone", 410, {}, URL, None, False),
    ("cross-host remains rejected", 429, {"Retry-After": "90"}, "https://example.net/1", None, False),
    ("transport failure", None, {}, URL, None, False),
]

def run(module, status, headers, final_url):
    calls = []
    def get(url, *, headers):
        calls.append(url)
        assert "Authorization" not in headers
        if status is None:
            raise requests.ConnectionError("controlled transport failure")
        response = requests.Response()
        response.status_code = status
        response.url = final_url
        response.headers.update(response_headers)
        response._content = json.dumps({"number": 1, "state": "open", "updated_at": "2026-10-04T09:00:00Z", "html_url": WEB}).encode()
        return response
    response_headers = headers
    module._github_get = get
    result = module.preflight_further_qualification(WEB, token="")
    assert calls == [URL]
    assert module.verify_receipt(result["receipt"])
    assert not module.is_clear_for_further_qualification(result["receipt"])
    return result, len(calls)

rows = []
for label, status, headers, final_url, delay, limited in cases:
    old, old_count = run(before, status, headers, final_url)
    new, new_count = run(after, status, headers, final_url)
    cooldown = new["receipt"]["live"].get("provider_cooldown")
    assert bool(cooldown) == limited, (label, cooldown)
    if cooldown:
        assert cooldown["rate_limited"] is True
        assert cooldown["retry_after_seconds"] == delay, (label, cooldown)
        if delay is not None and delay < 1000:
            recorded = datetime.fromisoformat(cooldown["retry_not_before"].replace("Z", "+00:00"))
            assert (recorded - NOW).total_seconds() == delay
        if label.endswith("overflow"):
            assert cooldown["retry_not_before"] is None
    scrubbed = json.loads(json.dumps(new))
    scrubbed["receipt"]["live"].pop("provider_cooldown", None)
    scrubbed["receipt"].pop("receipt_sha256")
    scrubbed["receipt"] = after._seal(scrubbed["receipt"])
    assert scrubbed == old, label
    rows.append({"case": label, "before_delay": old["receipt"]["live"].get("provider_cooldown"), "after_cooldown": cooldown, "before_requests": old_count, "after_requests": new_count, "classification": new["receipt"]["live"]["classification"], "pass": True})

result = {"python": sys.version.split()[0], "requests": requests.__version__, "network_attempts": 0, "passed": len(rows), "cases": rows, "sources_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in ("before.py", "after.py")}}
(ROOT / "results.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
