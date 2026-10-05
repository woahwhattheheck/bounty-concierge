#!/usr/bin/env python3
"""Resolution Radar: dependency-free, read-only Panta API adapter and UI server."""
from __future__ import annotations

import argparse
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from demo import demo_items

ROOT = Path(__file__).resolve().parent
API_BASE = 'https://live-api.panta.market/api/v1'
MAX_BODY = 2 * 1024 * 1024
FIELDS = ('marketId', 'category', 'title', 'description', 'phase', 'marketType', 'startTime',
          'endTime', 'resolutionTime', 'region', 'resolved', 'status', 'volumeUsdc',
          'totalVolumeUsdc', 'yesPrice', 'noPrice', 'primaryYesPrice', 'primaryNoPrice',
          'secondaryYesPrice', 'secondaryNoPrice', 'oracle', 'createdByPartner')


def iso_time(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


class ApiError(Exception):
    def __init__(self, status, code, message, retry_at=None):
        super().__init__(message)
        self.status, self.code, self.retry_at = status, code, retry_at


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward an API credential through a redirect.


def retry_deadline(value, now):
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 12:
        return now + int(value)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(now, date.timestamp())
    except (TypeError, ValueError, OverflowError, AttributeError):
        return now + 60


def reject_nonfinite(value):
    raise ValueError(f'Non-finite JSON value: {value}')


def project_row(row):
    if not isinstance(row, dict) or not isinstance(row.get('marketId'), str) or not row['marketId']:
        raise ApiError(502, 'BAD_RESPONSE', 'Panta returned a catalog row without a marketId.')
    return {key: row[key] for key in FIELDS if key in row}


class PantaCatalog:
    """One in-flight upstream GET, bounded cache, no automated retries."""
    def __init__(self, api_key='', *, opener=None, clock=time.time):
        self.api_key = api_key
        self.opener = opener or build_opener(NoRedirect())
        self.clock = clock
        self.cache = OrderedDict()
        self.lock = threading.Lock()
        self.retry_at = 0.0
        self.demo_now = int(clock())
        self.demo_rows = demo_items(self.demo_now)

    def get(self, path, query=None):
        if not self.api_key:
            raise ApiError(503, 'KEY_NOT_CONFIGURED', 'Live mode needs PANTA_API_KEY on the server. Demo data is available separately; it is never substituted for live data.')
        relative = path + ('?' + urlencode(query) if query else '')
        with self.lock:
            now = self.clock()
            if now < self.retry_at:
                raise ApiError(429, 'RATE_LIMITED', 'Panta requested a cooldown. No upstream request was sent.', self.retry_at)
            cached = self.cache.get(relative)
            if cached and now - cached['at'] < 60:
                self.cache.move_to_end(relative)
                return {'data': cached['data'], 'meta': self.meta(cached['at'], relative, True)}
            request = Request(API_BASE + relative, headers={'Accept': 'application/json', 'X-Api-Key': self.api_key})
            try:
                with self.opener.open(request, timeout=12) as response:
                    raw = response.read(MAX_BODY + 1)
                if len(raw) > MAX_BODY:
                    raise ApiError(502, 'BAD_RESPONSE', 'Panta returned more than the 2 MiB response limit.')
                data = json.loads(raw, parse_constant=reject_nonfinite)
            except HTTPError as exc:
                try:
                    if exc.code == 429:
                        self.retry_at = retry_deadline(exc.headers.get('Retry-After'), self.clock())
                        raise ApiError(429, 'RATE_LIMITED', 'Panta rate limited this read. Automatic retries are disabled.', self.retry_at) from None
                    if exc.code in (401, 403):
                        raise ApiError(exc.code, 'UPSTREAM_AUTH', 'Panta did not authorize this API key. No demo data was substituted.') from None
                    if exc.code == 404:
                        raise ApiError(404, 'MARKET_NOT_FOUND', 'Panta did not find this market.') from None
                    raise ApiError(502, 'UPSTREAM_ERROR', f'Panta returned HTTP {exc.code}; earlier displayed data has not been refreshed.') from None
                finally:
                    exc.close()
            except (URLError, TimeoutError, OSError):
                raise ApiError(502, 'UPSTREAM_UNREACHABLE', 'Panta could not be reached. Earlier displayed data has not been refreshed.') from None
            except (ValueError, UnicodeDecodeError):
                raise ApiError(502, 'BAD_RESPONSE', 'Panta returned an unreadable JSON response.') from None
            if path == '/markets/':
                if not isinstance(data, dict) or not isinstance(data.get('items'), list):
                    raise ApiError(502, 'BAD_RESPONSE', 'Panta returned an unexpected catalog shape.')
                cursor = data.get('nextCursor')
                if cursor is not None and (not isinstance(cursor, str) or not cursor or len(cursor) > 256):
                    raise ApiError(502, 'BAD_RESPONSE', 'Panta returned an invalid pagination cursor.')
                data = {'items': [project_row(row) for row in data['items']], 'nextCursor': cursor}
            else:
                data = project_row(data)
            received = self.clock()
            self.cache[relative] = {'at': received, 'data': data}
            self.cache.move_to_end(relative)
            while len(self.cache) > 128:
                self.cache.popitem(last=False)
            return {'data': data, 'meta': self.meta(received, relative, False)}

    def meta(self, at, relative, cached=False):
        return {'mode': 'live', 'fetchedAt': iso_time(at), 'endpoint': API_BASE + relative,
                'cacheHit': cached, 'catalogOnly': True, 'automaticPolling': False}

    def demo(self, market_id=None):
        if market_id is None:
            data = {'items': self.demo_rows, 'nextCursor': None}
        else:
            data = next((dict(m) for m in self.demo_rows if m['marketId'] == market_id), None)
            if data is None:
                raise ApiError(404, 'MARKET_NOT_FOUND', 'Demo market not found.')
            if data['phase'] in ('primary', 'secondary'):
                data.update(yesPrice='0.52', noPrice='0.48')
        return {'data': data, 'meta': {'mode': 'demo', 'fetchedAt': iso_time(self.demo_now),
                'endpoint': 'Synthetic local fixture; no Panta request', 'cacheHit': False,
                'catalogOnly': True, 'automaticPolling': False}}


def make_handler(catalog, default_mode='demo'):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'ResolutionRadar/1.0'

        def log_message(self, fmt, *args):
            pass  # No headers, credentials or request parameters in logs.

        def send_json(self, status, value, retry_at=None):
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if retry_at is not None:
                self.send_header('Retry-After', str(max(1, int(retry_at - time.time()) + 1)))
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                url = urlsplit(self.path)
                if url.path == '/api/config':
                    return self.send_json(200, {'defaultMode': default_mode, 'liveConfigured': bool(catalog.api_key)})
                if url.path == '/api/catalog':
                    query = parse_qs(url.query, keep_blank_values=True, max_num_fields=4)
                    if set(query) - {'cursor'} or any(len(v) != 1 for v in query.values()):
                        raise ApiError(400, 'BAD_QUERY', 'Only one optional cursor is accepted.')
                    cursor = query.get('cursor', [''])[0]
                    if len(cursor) > 256:
                        raise ApiError(400, 'BAD_QUERY', 'Cursor is too long.')
                    params = {'limit': '50'}
                    if cursor:
                        params['cursor'] = cursor
                    return self.send_json(200, catalog.get('/markets/', params))
                match = re.fullmatch(r'/api/markets/([1-9A-HJ-NP-Za-km-z]{32,44})', url.path)
                if match and not url.query:
                    return self.send_json(200, catalog.get('/markets/' + match[1] + '/'))
                if url.path == '/api/demo' and not url.query:
                    return self.send_json(200, catalog.demo())
                match = re.fullmatch(r'/api/demo/([1-9A-HJ-NP-Za-km-z]{32,44})', url.path)
                if match and not url.query:
                    return self.send_json(200, catalog.demo(match[1]))
                files = {'/': ('index.html', 'text/html'), '/app.mjs': ('app.mjs', 'text/javascript'),
                         '/model.mjs': ('model.mjs', 'text/javascript'), '/styles.css': ('styles.css', 'text/css')}
                if url.path not in files or url.query:
                    raise ApiError(404, 'NOT_FOUND', 'This read-only route does not exist.')
                filename, mime = files[url.path]
                body = (ROOT / 'public' / filename).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', mime + '; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)
            except ApiError as exc:
                self.send_json(exc.status, {'error': {'code': exc.code, 'message': str(exc), 'retryAt': exc.retry_at}}, exc.retry_at)
            except ValueError:
                self.send_json(400, {'error': {'code': 'BAD_QUERY', 'message': 'Invalid query parameters.'}})
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            self.send_json(405, {'error': {'code': 'READ_ONLY', 'message': 'Resolution Radar does not accept mutations.'}})

        do_PUT = do_PATCH = do_DELETE = do_POST
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--live', action='store_true', help='Open live mode; PANTA_API_KEY must already be configured.')
    args = parser.parse_args()
    catalog = PantaCatalog(os.environ.get('PANTA_API_KEY', '').strip())
    if args.live and not catalog.api_key:
        parser.error('--live requires the PANTA_API_KEY environment variable; no live fallback is provided.')
    server = ThreadingHTTPServer((args.host, args.port), make_handler(catalog, 'live' if args.live else 'demo'))
    print(f'Resolution Radar: http://{args.host}:{server.server_port} | {"live" if args.live else "DEMO (synthetic data)"}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
