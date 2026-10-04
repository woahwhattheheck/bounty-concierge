"""Compare the complete production URL validators and offline supply router.

No network requests, provider snapshots, or external work dispatch occurs.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import importlib.util
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git_blob(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=9)
    args = parser.parse_args()
    if not 3 <= args.repetitions <= 30:
        parser.error('repetitions must be between 3 and 30')
    before = load('url_scan_before', args.baseline)
    after = load('url_scan_after', args.candidate)
    from concierge import bounty_supply as supply

    # One exhaustive comparison of the replaced predicate, not a test suite.
    for value in range(sys.maxunicode + 1):
        char = chr(value)
        if bool(after._URL_WHITESPACE_RE.search(char)) != (char.isspace() or value == 0x7f):
            raise AssertionError(f'Predicate mismatch at U+{value:04X}')

    urls = ['https://github.com/example/project/issues/1',
            'https://docs.example.org/guide', '', None, 7,
            ' https://github.com/example/project/issues/1',
            'http://github.com/example/project/issues/1',
            'https://github.com:443/example/project/issues/1',
            'https://github.com/example/project/issues/1?view=1',
            'https://github.com/example/project/issues/1#notes',
            'https://github.com/example/project/issues/1/']
    urls += ['https://github.com/example/' + chr(i) + 'project/issues/1'
             for i in (9, 10, 13, 28, 32, 127, 160, 0x1680, 0x2003, 0x2028, 0x202f, 0x3000)]
    def outcome(module, url):
        try:
            return ('ok', module._strict_url_parts(url, 'source_url'))
        except Exception as exc:
            return (type(exc).__name__, str(exc))
    for url in urls:
        if outcome(before, url) != outcome(after, url):
            raise AssertionError('Complete validator output/error changed')

    # Compare complete receipt/error behavior, including the exact-type guard.
    issue_url = 'https://github.com/example/project/issues/1'
    timestamp = '2026-10-04T11:00:00Z'
    text = 'Implement a CSV export.'
    request = {'schema': before.SCHEMA, 'issue_url': issue_url, 'source_url': issue_url,
               'source_text': text, 'source_content_sha256': hashlib.sha256(text.encode()).hexdigest(),
               'observed_at': timestamp, 'evaluated_at': timestamp}
    evaluated = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    class TextSubclass(str):
        pass
    compiled_cases = 0
    for field in ('source_url', 'issue_url'):
        for url in urls + [TextSubclass(issue_url), {'url': issue_url}, [issue_url]]:
            current = dict(request, **{field: url})
            values = []
            for module in (before, after):
                try:
                    value = ('ok', module._compile_bounty_acceptance_safety_gate_at(current, evaluated))
                except Exception as exc:
                    value = (type(exc).__name__, str(exc))
                values.append(value)
            if values[0] != values[1]:
                raise AssertionError(f'Complete gate behavior changed for {field}')
            compiled_cases += 1
    validation_calls = {}
    for variant, module in [('before', before), ('after', after)]:
        original_validator = module._strict_url_parts
        fields = []
        def counted(value, field, original=original_validator):
            fields.append(field)
            return original(value, field)
        module._strict_url_parts = counted
        try:
            module._compile_bounty_acceptance_safety_gate_at(request, evaluated)
        finally:
            module._strict_url_parts = original_validator
        validation_calls[variant] = fields

    short = 'example/project'
    long = 'sample-' + 'x' * 30 + '/' + 'project-' + 'y' * 88
    base = {'title': 'Bounty: $75', 'body': 'Implement a CSV export. /bounty $75',
            'comments': [], 'labels': [], 'observed_at': '2026-10-04T11:00:00Z',
            'canonical_audit': {'issue_state': 'open', 'open_pr_count': 0,
                                'stale_listing_signal': False, 'search_truncated': False}}
    results = {}
    original = supply._compile_bounty_acceptance_safety_gate_at
    try:
        for name, repo, mixed in [('short_urls', short, False), ('long_urls', long, False), ('mixed_routes', short, True)]:
            rows = []
            for i in range(2000):
                row = copy.deepcopy(base)
                row.update(repo=repo, number=i + 1)
                if mixed:
                    if i % 4 == 0:
                        row['canonical_audit']['issue_state'] = 'closed'
                    elif i % 4 == 1:
                        row['observed_at'] = '2026-10-03T11:00:00Z'
                    elif i % 4 == 2:
                        row['title'] = 'Bounty: $15'
                        row['body'] = 'Implement a CSV export. /bounty $15'
                rows.append(row)
            pristine = copy.deepcopy(rows)
            timings = {'before': [], 'after': []}
            reference = None
            for iteration in range(args.repetitions + 1):
                order = [('before', before), ('after', after)]
                if iteration % 2:
                    order.reverse()
                for variant, module in order:
                    supply._compile_bounty_acceptance_safety_gate_at = module._compile_bounty_acceptance_safety_gate_at
                    started = time.perf_counter()
                    value = supply.route_supply(rows, evaluated_at='2026-10-04T11:01:00Z')
                    elapsed = time.perf_counter() - started
                    if iteration:
                        timings[variant].append(elapsed)
                    if reference is None:
                        reference = value
                    elif value != reference:
                        raise AssertionError(f'Routing output or receipt changed: {name}/{variant}')
            if rows != pristine:
                raise AssertionError('Routing mutated input')
            medians = {key: statistics.median(times) for key, times in timings.items()}
            results[name] = {'rows': len(rows), 'seconds': timings, 'median_seconds': medians,
                             'reduction_percent': 100 * (1 - medians['after'] / medians['before']),
                             'counts': reference['counts'], 'receipt_sha256': reference['receipt_sha256'],
                             'identical_outputs_and_receipts': True, 'input_unchanged': True}
    finally:
        supply._compile_bounty_acceptance_safety_gate_at = original
    result = {'scope': 'Full production modules; synthetic offline routing; no network requests.',
              'recorded_at': datetime.now(timezone.utc).isoformat(),
              'python': platform.python_version(), 'platform': platform.platform(),
              'baseline_git_blob': git_blob(args.baseline.read_bytes()),
              'candidate_git_blob': git_blob(args.candidate.read_bytes()),
              'unicode_predicate_equivalent_through': sys.maxunicode,
              'validator_comparisons': len(urls), 'complete_gate_comparisons': compiled_cases,
              'same_url_validation_fields': validation_calls, 'repetitions': args.repetitions,
              'workloads': results}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
