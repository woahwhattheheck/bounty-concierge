"""Focused adapter and loopback HTTP checks; no live credentials or external calls."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import json
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import PantaCatalog, ApiError, make_handler, ThreadingHTTPServer, retry_deadline


class Replay:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload, error, []

    def open(self, request, timeout):
        self.calls.append((request.full_url, request.get_method(), timeout))
        if self.error:
            raise HTTPError(request.full_url, self.error, 'replay', {'Retry-After': '120'}, BytesIO())
        return BytesIO(json.dumps(self.payload).encode())


def test_single_flight_cache_projection_and_expiration():
    clock = [1791154800.0]
    replay = Replay({'items': [{'marketId': 'A' * 32, 'title': 'Recorded fixture', 'billingSecret': 'DO_NOT_COPY'}], 'nextCursor': 'opaque+token/='})
    catalog = PantaCatalog('test-placeholder-not-a-credential', opener=replay, clock=lambda: clock[0])
    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(lambda _: catalog.get('/markets/', {'limit': '50'}), range(8)))
    assert len(replay.calls) == 1
    assert sum(not item['meta']['cacheHit'] for item in responses) == 1
    assert 'billingSecret' not in responses[0]['data']['items'][0]
    assert responses[0]['data']['nextCursor'] == 'opaque+token/='
    assert all(item['meta']['fetchedAt'] == responses[0]['meta']['fetchedAt'] for item in responses)
    clock[0] += 61
    catalog.get('/markets/', {'limit': '50'})
    assert len(replay.calls) == 2


def test_rate_limit_is_shared_across_paths_and_never_retried_automatically():
    clock = [1791154800.0]
    replay = Replay(error=429)
    catalog = PantaCatalog('test-placeholder', opener=replay, clock=lambda: clock[0])
    with pytest.raises(ApiError) as initial:
        catalog.get('/markets/')
    assert initial.value.status == 429 and initial.value.retry_at == clock[0] + 120
    with pytest.raises(ApiError):
        catalog.get('/markets/' + 'A' * 32 + '/')
    assert len(replay.calls) == 1
    clock[0] += 121
    with pytest.raises(ApiError):
        catalog.get('/markets/')
    assert len(replay.calls) == 2
    assert retry_deadline('Sun, 04 Oct 2026 23:05:00 GMT', 1791154800) == 1791155100


def test_missing_key_auth_failure_and_bad_json_never_fall_back_to_demo():
    with pytest.raises(ApiError) as absent:
        PantaCatalog().get('/markets/')
    assert absent.value.status == 503
    with pytest.raises(ApiError) as auth:
        PantaCatalog('placeholder', opener=Replay(error=401)).get('/markets/')
    assert auth.value.status == 401
    for payload in ({'items': [{}]}, {'items': [], 'nextCursor': []}, {'items': [{'marketId': 'A', 'volumeUsdc': float('nan')}]}):
        with pytest.raises(ApiError) as malformed:
            PantaCatalog('placeholder', opener=Replay(payload)).get('/markets/')
        assert malformed.value.status == 502


def test_loopback_ui_demo_detail_and_mutation_rejection():
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(PantaCatalog()))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with urlopen(base + '/') as response:
            assert 'Powered by' in response.read().decode()
            assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
        with urlopen(base + '/api/demo') as response:
            page = json.load(response)
        assert page['meta']['mode'] == 'demo' and len(page['data']['items']) == 8
        market_id = page['data']['items'][0]['marketId']
        with urlopen(base + '/api/demo/' + market_id) as response:
            detail = json.load(response)
        assert detail['data']['marketId'] == market_id and detail['data']['yesPrice'] == '0.52'
        for path, method, status in [('/api/catalog', 'GET', 503), ('/server.py', 'GET', 404), ('/api/demo', 'POST', 405), ('/api/catalog?cursor=a&cursor=b', 'GET', 400)]:
            with pytest.raises(HTTPError) as failure:
                urlopen(Request(base + path, method=method))
            assert failure.value.code == status
            failure.value.close()
    finally:
        server.shutdown(); thread.join(timeout=2); server.server_close()
