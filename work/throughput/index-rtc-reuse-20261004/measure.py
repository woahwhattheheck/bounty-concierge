"""Measure actual collection against retained issue text over loopback HTTP."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="46f3b203bd816543fb8cddf38e77cb3f558e9af6")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))

    def retained(path):
        return subprocess.check_output(["git", "show", f"{args.baseline}:{path}"], cwd=root)

    source_path = "concierge/bounty_index.py"
    before_source = retained(source_path)
    after_source = (root / source_path).read_bytes()
    index_bytes = retained("data/bounty_index.json")
    index = json.loads(index_bytes)
    pages = defaultdict(list)
    for bounty in index["bounties"]:
        pages[bounty["repo"]].append({
            "number": bounty["number"], "title": bounty["title"], "body": bounty["body"],
            "html_url": bounty["url"], "labels": [{"name": name} for name in bounty["labels"]],
            "created_at": bounty["created_at"],
        })
    repos = [entry["repo"] for entry in index["repositories"]]
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            repo = parsed.path.removeprefix("/repos/").removesuffix("/issues")
            page = int(parse_qs(parsed.query)["page"][0])
            calls.append((repo, page))
            start = (page - 1) * 100
            payload = json.dumps(pages[repo][start:start + 100]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            if len(pages[repo]) > start + 100:
                self.send_header("Link", '<https://api.github.com/next>; rel="next"')
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    class ReplaySession(requests.Session):
        def __init__(self):
            super().__init__()
            self.trust_env = False

        def get(self, url, **kwargs):
            assert url.startswith("https://api.github.com/repos/")
            return super().get(f"http://127.0.0.1:{server.server_port}{urlsplit(url).path}", **kwargs)

    try:
        with tempfile.TemporaryDirectory(prefix="rtc-reuse-", dir="/dev/shm") as directory:
            modules = {}
            parse_calls = {"before": 0, "after": 0}
            for name, source in (("before", before_source), ("after", after_source)):
                path = Path(directory) / f"{name}.py"
                path.write_bytes(source)
                spec = importlib.util.spec_from_file_location(f"index_{name}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.requests = SimpleNamespace(Session=ReplaySession, RequestException=requests.RequestException,
                                                  HTTPError=requests.HTTPError)
                original_parse = module.parse_reward

                def counted(*args, name=name, parse=original_parse):
                    parse_calls[name] += 1
                    return parse(*args)

                module.parse_reward = counted
                modules[name] = module

            samples = {name: [] for name in modules}
            expected = None
            expected_calls = None
            for sample in range(8):
                order = ("before", "after") if sample % 2 == 0 else ("after", "before")
                for name in order:
                    calls.clear()
                    parse_calls[name] = 0
                    wall_start, cpu_start = time.perf_counter(), time.process_time()
                    result = modules[name].fetch_bounties_report(repos, token="retained-replay", cache_dir=False)
                    cpu_ms = (time.process_time() - cpu_start) * 1000
                    wall_ms = (time.perf_counter() - wall_start) * 1000
                    for key in ("started_at", "updated_at"):
                        result.pop(key)
                    canonical = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
                    if expected is None:
                        expected, expected_calls = canonical, list(calls)
                    assert canonical == expected, f"collector output changed for {name}"
                    assert calls == expected_calls, f"request order changed for {name}"
                    if sample:
                        samples[name].append({"wall_ms": wall_ms, "process_cpu_ms": cpu_ms})
            report = {
                "baseline_commit": args.baseline,
                "baseline_source_sha256": hashlib.sha256(before_source).hexdigest(),
                "candidate_source_sha256": hashlib.sha256(after_source).hexdigest(),
                "reward_extractor_sha256": hashlib.sha256((root / "concierge/reward_evidence.py").read_bytes()).hexdigest(),
                "retained_input": "data/bounty_index.json",
                "retained_input_sha256": hashlib.sha256(index_bytes).hexdigest(),
                "retained_observed_at": index["updated_at"],
                "retained_issue_count": len(index["bounties"]),
                "title_body_characters": sum(len(row["title"]) + len(row["body"]) for row in index["bounties"]),
                "python": sys.version,
                "measurement": "Actual fetch_bounties_report and Requests via loopback; real retained issue text, reconstructed provider page envelopes. Process CPU includes the loopback server.",
                "limitations": "No live GitHub, WAN, quota savings, fleet throughput or freshly eligible bounty claim. Request count is unchanged. Only report observation clocks are excluded from equality.",
                "outputs_equal": True,
                "output_sha256": hashlib.sha256(expected).hexdigest(),
                "requests_each": len(expected_calls),
                "parse_reward_calls": parse_calls,
                "samples": samples,
                "medians_ms": {
                    name: {key: statistics.median(row[key] for row in rows) for key in ("wall_ms", "process_cpu_ms")}
                    for name, rows in samples.items()
                },
            }
            print(json.dumps(report, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


if __name__ == "__main__":
    main()
