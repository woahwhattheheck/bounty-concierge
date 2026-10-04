"""Replay explicit GitHub quota errors through complete live-status modules.

The private HTTP seam returns real Requests.Response objects containing retained
or controlled bytes. Network, package bootstrap and live provider acceptance are
outside this check; only the ambient token configuration is supplied separately.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import sys
import types
import requests

NOW = datetime(2026, 10, 4, 12, 11, tzinfo=timezone.utc)
WEB = 'https://github.com/example/project/issues/1'
API = 'https://api.github.com/repos/example/project/issues/1'


def load(name, path):
    module = types.ModuleType(name)
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    module._now_utc = lambda: NOW
    return module


def identity(path):
    data = path.read_bytes()
    return {'sha256': hashlib.sha256(data).hexdigest(),
            'git_blob': hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    def no_network(*args, **kwargs):
        raise AssertionError('provider network is outside this replay')
    socket.socket.connect = no_network
    package = types.ModuleType('concierge')
    package.__path__ = []
    config = types.ModuleType('concierge.config')
    config.GITHUB_TOKEN = ''
    sys.modules['concierge'] = package
    sys.modules['concierge.config'] = config
    modules = [('before', load('before', args.before)), ('after', load('after', args.after))]
    error = json.loads((Path(__file__).parent / 'provider_error.json').read_text())
    cases = [
        ('captured secondary, no wait header', 403, {}, error, True),
        ('secondary with ordinary remaining quota', 403, {'X-RateLimit-Remaining': '50'}, error, True),
        ('explicit primary limit, no wait header', 403, {}, {'message': 'API rate limit exceeded for fixture'}, True),
        ('permission with rate-limit documentation URL', 403, {},
         {'message': 'Resource not accessible by integration', 'documentation_url': error['documentation_url']}, False),
        ('malformed JSON', 403, {}, b'not-json', False),
        ('nonobject JSON', 403, {}, ['secondary rate limit'], False),
        ('existing Retry-After seconds', 403, {'Retry-After': '45'}, {}, True),
        ('existing unknown 429', 429, {}, {}, True),
    ]
    rows = []
    for label, status, headers, body, limited in cases:
        results = {}
        for name, module in modules:
            calls = []
            def get(url, *, headers):
                calls.append(url)
                assert 'Authorization' not in headers
                response = requests.Response()
                response.status_code = status
                response.url = API
                response.headers.update(response_headers)
                response._content = body if isinstance(body, bytes) else json.dumps(body).encode()
                return response
            response_headers = headers
            module._github_get = get
            result = module.preflight_further_qualification(WEB, token='')
            assert calls == [API]
            assert module.verify_receipt(result['receipt'])
            assert not module.is_clear_for_further_qualification(result['receipt'])
            results[name] = result
        before, after = results['before'], results['after']
        old_cooldown = before['receipt']['live'].get('provider_cooldown')
        cooldown = after['receipt']['live'].get('provider_cooldown')
        assert bool(cooldown) == limited, (label, cooldown)
        assert after['receipt']['live']['classification'] == 'UNVERIFIABLE'
        assert after['receipt']['live']['reason_code'] == f'GITHUB_HTTP_{status}'
        if label.startswith(('captured', 'secondary', 'explicit')):
            assert old_cooldown is None
            assert cooldown == {'rate_limited': True, 'retry_after_seconds': None, 'retry_not_before': None}
            scrubbed = json.loads(json.dumps(after))
            scrubbed['receipt']['live'].pop('provider_cooldown')
            scrubbed['receipt'].pop('receipt_sha256')
            scrubbed['receipt'] = modules[1][1]._seal(scrubbed['receipt'])
            assert scrubbed == before
        else:
            assert after == before, label
        rows.append({'case': label, 'before_cooldown': old_cooldown, 'after_cooldown': cooldown,
                     'http_seam_calls_per_source': 1, 'prior_decision_preserved': True, 'pass': True})
    result = {'python': sys.version.split()[0], 'requests': requests.__version__,
              'network_attempts': 0, 'passed': len(rows), 'cases': rows,
              'sources': {'before': identity(args.before), 'after': identity(args.after)}}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': len(rows), 'network_attempts': 0, 'sources': result['sources']}))


if __name__ == '__main__':
    main()
