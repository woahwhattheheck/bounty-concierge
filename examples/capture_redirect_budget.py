"""Focused offline verification using real Requests and loopback HTTP servers."""
import hashlib
import argparse
import json
from pathlib import Path
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import unittest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    '--repo-root', type=Path, default=Path(__file__).resolve().parents[1],
    help='Repository root containing concierge (defaults to this example parent)',
)
args = parser.parse_args()
sys.path.insert(0, str(args.repo_root))
import requests
from concierge import bounty_capture_batch
BatchSession = bounty_capture_batch._BatchSession

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.observed.append((self.path, dict(self.headers)))
        routes = {'/start': '/final', '/chain/2': '/chain/1', '/chain/1': '/final',
                  '/quota-start': '/final', '/rate-start': '/rate',
                  '/cross': self.server.cross_url}
        if self.path in routes:
            self.send_response(302)
            self.send_header('Location', routes[self.path])
            self.send_header('Set-Cookie', 'capture_fixture=retained; Path=/')
            if self.path == '/quota-start':
                self.send_header('X-RateLimit-Remaining', '0')
                self.send_header('Retry-After', '60')
            self.send_header('Content-Length', '0')
            self.end_headers()
        else:
            body = b'{"ok":true}'
            self.send_response(429 if self.path == '/rate' else 200)
            if self.path == '/rate':
                self.send_header('Retry-After', '60')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    def log_message(self, *args):
        pass

class RedirectBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servers = [ThreadingHTTPServer(('127.0.0.1', 0), Handler) for _ in range(2)]
        cls.threads = []
        for server in cls.servers:
            server.observed = []
            server.cross_url = 'http://localhost:%d/final' % cls.servers[1].server_port
            thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
            thread.start()
            cls.threads.append(thread)
        cls.origin = 'http://127.0.0.1:%d' % cls.servers[0].server_port
    @classmethod
    def tearDownClass(cls):
        for server, thread in zip(cls.servers, cls.threads):
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)
    def setUp(self):
        for server in self.servers:
            server.observed.clear()
        self.session = requests.Session()
        self.session.trust_env = False
        self.addCleanup(self.session.close)
    def paths(self, server=0):
        return [row[0] for row in self.servers[server].observed]
    def test_budget_one_stops_before_next_request_and_hook(self):
        seen = []
        def hook(response, **kwargs):
            seen.append(response)
        original_hooks = {'response': [hook]}
        self.session.hooks = original_hooks
        batch = BatchSession(self.session, 1)
        with self.assertRaises(requests.RequestException):
            batch.get(self.origin + '/start', timeout=2, stream=True, hooks={'response': []})
        self.assertEqual((self.paths(), batch.request_count, batch.failure),
                         (['/start'], 1, {'code': 'REQUEST_LIMIT'}))
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].raw.closed)
        self.assertIs(self.session.hooks, original_hooks)
        self.assertEqual(self.session.hooks['response'], [hook])
    def test_sufficient_budget_preserves_cookie_settings_and_hook_replacement(self):
        seen = []
        replacements = []
        def hook(response, **settings):
            seen.append((response, settings.copy()))
            if response.url.endswith('/start'):
                replacement = requests.Response()
                replacement.__dict__.update(response.__dict__)
                replacements.append(replacement)
                return replacement
        self.session.hooks['response'].append(hook)
        original_hook_list = self.session.hooks['response']
        self.session.verify = False
        self.session.stream = True
        batch = BatchSession(self.session, 2)
        response = batch.get(self.origin + '/start', timeout=(1, 2), hooks=None)
        self.addCleanup(response.close)
        self.assertEqual((self.paths(), batch.request_count, response.status_code),
                         (['/start', '/final'], 2, 200))
        self.assertEqual(response.history, replacements)
        self.assertTrue(response.history[0].raw.closed)
        self.assertIn('capture_fixture=retained', self.servers[0].observed[1][1].get('Cookie', ''))
        for _, settings in seen:
            self.assertEqual((settings['timeout'], settings['verify'], settings['stream']),
                             ((1, 2), False, True))
        self.assertIs(self.session.hooks['response'], original_hook_list)
        self.assertEqual(original_hook_list, [hook])
    def test_chain_history_and_redirect_limits(self):
        for limit, budget, path, wanted_count, error in (
            (30, 3, '/chain/2', 3, False),
            (1, 10, '/chain/2', 2, True),
            (0, 10, '/start', 1, True),
        ):
            with self.subTest(max_redirects=limit):
                self.servers[0].observed.clear()
                self.session.max_redirects = limit
                batch = BatchSession(self.session, budget)
                if error:
                    with self.assertRaises(requests.TooManyRedirects) as caught:
                        batch.get(self.origin + path, timeout=2)
                    self.assertTrue(caught.exception.response.raw.closed)
                    self.assertEqual(batch.failure['error_type'], 'TooManyRedirects')
                else:
                    response = batch.get(self.origin + path, timeout=2)
                    self.assertEqual([item.status_code for item in response.history], [302, 302])
                    self.assertEqual(response.history[1].history, response.history[:1])
                    response.close()
                self.assertEqual((len(self.paths()), batch.request_count), (wanted_count, wanted_count))
    def test_exhausted_redirect_does_not_send_next_hop(self):
        batch = BatchSession(self.session, 10)
        with self.assertRaises(requests.RequestException):
            batch.get(self.origin + '/quota-start', timeout=2)
        self.assertEqual((self.paths(), batch.request_count, batch.failure),
                         (['/quota-start'], 1, {'code': 'RATE_LIMITED'}))
        self.assertEqual(batch.retry_after_seconds, 60)
    def test_redirected_rate_limit_hook_error_stops_future_gets(self):
        seen = []
        def raising_hook(response, **kwargs):
            seen.append(response)
            response.raise_for_status()
        original_hooks = {'response': [raising_hook]}
        self.session.hooks = original_hooks
        batch = BatchSession(self.session, 10)
        with self.assertRaises(requests.HTTPError):
            batch.get(self.origin + '/rate-start', timeout=2, stream=True)
        self.assertEqual((self.paths(), batch.request_count), (['/rate-start', '/rate'], 2))
        self.assertTrue(batch.rate_limited)
        self.assertEqual(batch.retry_after_seconds, 60)
        self.assertEqual(batch.failure, {'code': 'RATE_LIMITED', 'http_status': 429})
        with self.assertRaises(requests.RequestException):
            batch.get(self.origin + '/final', timeout=2)
        self.assertEqual((len(self.paths()), len(seen), batch.request_count), (2, 2, 2))
        self.assertTrue(all(response.raw.closed for response in seen))
        self.assertIs(self.session.hooks, original_hooks)
        self.assertEqual(original_hooks['response'], [raising_hook])
    def test_cross_host_strips_authorization_without_changing_session(self):
        fixture = 'Bearer loopback-fixture'
        self.session.headers['Authorization'] = fixture
        batch = BatchSession(self.session, 2)
        response = batch.get(self.origin + '/cross', timeout=2)
        response.close()
        self.assertEqual((self.paths(), self.paths(1), batch.request_count), (['/cross'], ['/final'], 2))
        self.assertEqual(self.servers[0].observed[0][1]['Authorization'], fixture)
        self.assertNotIn('Authorization', self.servers[1].observed[0][1])
        self.assertEqual(self.session.headers['Authorization'], fixture)
    def test_plain_get_and_get_only_transport_keep_existing_behavior(self):
        session_calls = []
        request_calls = []
        self.session.hooks['response'].append(lambda response, **kwargs: session_calls.append(response.status_code))
        hooks = {'response': [lambda response, **kwargs: request_calls.append(response.status_code)]}
        batch = BatchSession(self.session, 1)
        response = batch.get(self.origin + '/final', timeout=2, hooks=hooks)
        self.assertEqual((self.paths(), batch.request_count, response.json()), (['/final'], 1, {'ok': True}))
        self.assertEqual((session_calls, request_calls), ([], [200]))
        response.close()
        class GetOnly:
            def __init__(self):
                self.calls = 0
            def get(self, url, *, headers, timeout):
                self.calls += 1
                response = requests.Response()
                response.status_code = 200
                response._content = b'{}'
                return response
        provider = GetOnly()
        custom = BatchSession(provider, 1)
        custom.get('fixture:only-get', headers={}, timeout=2)
        self.assertEqual((provider.calls, custom.request_count), (1, 1))

if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RedirectBudgetTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    source = Path(bounty_capture_batch.__file__)
    raw = source.read_bytes()
    print(json.dumps({'tests_run': result.testsRun, 'successful': result.wasSuccessful(),
                      'python': sys.version.split()[0], 'requests': requests.__version__,
                      'source_sha256': hashlib.sha256(raw).hexdigest()}, sort_keys=True))
    raise SystemExit(0 if result.wasSuccessful() else 1)
