"""Focused public-path proof; real localhost HTTP, no GitHub traffic."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
from threading import Thread
from types import ModuleType
from urllib.parse import parse_qs, urlsplit

import requests

from concierge import bounty_contract as public
from concierge import bounty_contract_hardening as hardening
from concierge import bounty_contract_live as candidate
from concierge.bounty_contract_common import BountyContractError

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('fixture', ROOT / 'work/throughput/contract-session-reuse-20261003/benchmark.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
baseline = ModuleType('contract_pagination_baseline')
exec(compile(subprocess.check_output(['git', 'show', '4ec4bee4b75fc1db313e183b343457a536286144:concierge/bounty_contract_live.py'], cwd=ROOT), 'baseline_live.py', 'exec'), baseline.__dict__)
comments = []
for number in range(1, 1002):
    comment = deepcopy(fixture.COMMENTS[0])
    comment.update(id=number, node_id=f'IC_{number}', body=f'Terms {number}')
    comments.append(comment)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def log_message(self, *_):
        pass

    def do_GET(self):
        server = self.server
        parsed = urlsplit(self.path)
        server.calls.append(self.path)
        headers = {}
        if parsed.path == fixture.ISSUE_PATH:
            payload = deepcopy(fixture.ISSUE)
            payload['comments'] = server.expected_count
        elif parsed.path == fixture.ISSUE_PATH + '/comments':
            page = int(parse_qs(parsed.query)['page'][0])
            if page == 1:
                server.traversals += 1
            payload = deepcopy(comments[(page - 1) * 100:min(page * 100, server.count)])
            if server.scenario == 'same_second_edit' and server.traversals == 2:
                payload[0]['body'] += ' Changed.'
            if page * 100 < server.count:
                target = 'https://api.github.com' + fixture.ISSUE_PATH + '/comments'
                if server.scenario == 'numeric_alias':
                    target = f'https://api.github.com/repositories/123/issues/{fixture.ISSUE_NUMBER}/comments'
                headers['Link'] = f'<{target}?per_page=100&page={page + 1}>; rel="next"'
            elif page > 1:
                headers['link'] = (
                    f'<https://api.github.com{fixture.ISSUE_PATH}/comments?per_page=100&page={page - 1}>; rel="prev", '
                    f'<https://api.github.com{fixture.ISSUE_PATH}/comments?per_page=100&page=1>; rel="first"'
                )
            if server.scenario == 'foreign_host':
                headers['Link'] = '<https://foreign.invalid/comments?per_page=100&page=2>; rel="next"'
            if server.scenario == 'foreign_repo':
                headers['Link'] = '<https://api.github.com/repos/foreign/repo/issues/1/comments?per_page=100&page=2>; rel="next"'
            if server.scenario == 'malformed_link':
                headers['Link'] = 'not a Link header'
        else:
            raise AssertionError(self.path)
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
server.daemon_threads = True
worker = Thread(target=server.serve_forever, daemon=True)
worker.start()


class HeaderlessResponse:
    def __init__(self, response):
        self.json = response.json
        self.raise_for_status = response.raise_for_status


class LocalSession(requests.Session):
    def __init__(self, headerless=False):
        super().__init__()
        self.trust_env = False
        self.headerless = headerless
        self.original_urls = []

    def get(self, url, **kwargs):
        assert url.startswith('https://api.github.com' + fixture.ISSUE_PATH)
        self.original_urls.append(url)
        response = super().get(f'http://127.0.0.1:{server.server_port}' + urlsplit(url).path, **kwargs)
        return HeaderlessResponse(response) if self.headerless else response


def run(which, count, max_pages, scenario='stable', expected_count=None, headerless=False, receipt=None):
    hardening._fetch_comments = baseline._fetch_comments if which == 'before' else candidate._fetch_comments
    server.count = count
    server.expected_count = count if expected_count is None else expected_count
    server.scenario = scenario
    server.calls = []
    server.traversals = 0
    with LocalSession(headerless) as session:
        try:
            if receipt is None:
                result = public.capture_contract(fixture.REPO, fixture.ISSUE_NUMBER, 'fixture-only-token', session=session, max_pages=max_pages, captured_at=fixture.STAMP)
                status = 'OK'
            else:
                result = public.verify_contract(receipt, 'fixture-only-token', session=session, max_pages=max_pages, checked_at=fixture.STAMP)
                status = result['disposition']
        except BountyContractError as error:
            result = None
            status = getattr(error, 'reason_code', type(error).__name__)
        assert all(url in (fixture.ISSUE_URL, fixture.ISSUE_URL + '/comments') for url in session.original_urls)
    metrics = {'implementation': which, 'comments': count, 'max_pages': max_pages, 'scenario': scenario, 'requests': len(server.calls), 'comment_requests': sum('/comments?' in call for call in server.calls), 'status': status}
    return result, metrics


report = {'method': 'Actual public capture_contract/verify_contract over real HTTP/1.1 on localhost; no GitHub requests, synthetic latency, or retries.', 'cases': []}
try:
    for count, max_pages in ((100, 1), (100, 2), (1000, 10), (1000, 11), (101, 2), (101, 1), (0, 1)):
        before, bm = run('before', count, max_pages)
        after, am = run('after', count, max_pages)
        if before is not None:
            assert before == after, (count, max_pages)
        if (count, max_pages) in ((100, 1), (1000, 10)):
            assert bm['status'] == 'LIVE_EVIDENCE_INCOMPLETE' and am['status'] == 'OK'
        if (count, max_pages) in ((100, 2), (1000, 11)):
            assert bm['requests'] - am['requests'] == 2
        report['cases'].extend([bm, am])
    capture, metric = run('after', 100, 1)
    verified, verify_metric = run('after', 100, 1, receipt=capture)
    assert verified['disposition'] == 'UNCHANGED'
    report['verification'] = verify_metric
    for scenario, count, max_pages, reason in (
        ('same_second_edit', 100, 1, 'LIVE_GENERATION_UNSTABLE'),
        ('foreign_host', 101, 2, 'LIVE_EVIDENCE_INVALID'),
        ('foreign_repo', 101, 2, 'LIVE_EVIDENCE_INVALID'),
        ('malformed_link', 101, 2, 'LIVE_EVIDENCE_INVALID'),
    ):
        _, metric = run('after', count, max_pages, scenario)
        assert metric['status'] == reason, metric
        report['cases'].append(metric)
    _, metric = run('after', 100, 1, expected_count=101)
    assert metric['status'] == 'LIVE_EVIDENCE_INCOMPLETE', metric
    report['cases'].append(dict(metric, expected_comments=101))
    _, metric = run('after', 101, 2, 'numeric_alias')
    assert metric['status'] == 'OK' and metric['requests'] == 7, metric
    report['cases'].append(metric)
    legacy, metric = run('after', 100, 2, headerless=True)
    assert legacy == capture and metric['requests'] == 7, metric
    report['cases'].append(dict(metric, headerless_transport=True))
    print(json.dumps(report, indent=2))
finally:
    hardening._fetch_comments = candidate._fetch_comments
    server.shutdown()
    server.server_close()
    worker.join()
