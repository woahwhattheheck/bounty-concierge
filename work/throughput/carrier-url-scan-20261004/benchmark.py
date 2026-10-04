#!/usr/bin/env python3
"""Compare two complete census modules on synthetic retained snapshots; no network."""
import argparse
import hashlib
import importlib.util
import json
import platform
import statistics
import sys
from pathlib import Path
from time import perf_counter_ns


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blob(path):
    raw = Path(path).read_bytes()
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def request(count):
    return {
        'schema': 'grantfox-carrier-census/v1',
        'canonical_issue_url': 'https://github.com/ExampleOrg/ExampleRepo/issues/1',
        'observed_at': '2026-10-04T12:00:00Z',
        'evaluated_at': '2026-10-04T12:01:00Z',
        'carriers': [
            {'pr_url': f'https://github.com/ExampleOrg/ExampleRepo/pull/{index + 1}',
             'state': 'closed', 'issue_relation': 'closes',
             'process_disposition': 'closed_unassigned' if index % 2 else 'normal',
             'head_sha': 'a' * 40}
            for index in range(count)
        ],
    }


def elapsed(module, payload, repeats):
    started = perf_counter_ns()
    for _ in range(repeats):
        module.compile_grantfox_carrier_census(payload)
    return (perf_counter_ns() - started) / 1e6


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before')
    parser.add_argument('after')
    parser.add_argument('--repeats', type=int, default=200)
    parser.add_argument('--pairs', type=int, default=7)
    args = parser.parse_args()
    if args.repeats < 1 or args.pairs < 1:
        parser.error('repeats and pairs must be positive')
    before, after = load(args.before, 'census_before'), load(args.after, 'census_after')
    result = {
        'python': sys.version, 'platform': platform.platform(),
        'before_blob': blob(args.before), 'after_blob': blob(args.after),
        'repeats_per_sample': args.repeats, 'alternating_pairs': args.pairs,
        'scope': 'complete compiler including receipt serialization/hash; warm retained synthetic snapshots; no network',
        'workloads': [],
    }
    for count in (1, 10, 100):
        payload = request(count)
        old, new = before.compile_grantfox_carrier_census(payload), after.compile_grantfox_carrier_census(payload)
        assert old == new, 'complete receipt changed'
        assert before.verify_carrier_census_receipt(old) and after.verify_carrier_census_receipt(old)
        for module in (before, after):
            elapsed(module, payload, 10)
        samples = {'before': [], 'after': []}
        for pair in range(args.pairs):
            order = [('before', before), ('after', after)]
            if pair % 2:
                order.reverse()
            for key, module in order:
                samples[key].append(elapsed(module, payload, args.repeats))
        medians = {key: statistics.median(values) for key, values in samples.items()}
        result['workloads'].append({
            'carriers': count, 'receipts_equal': True, 'receipt_sha256': old['carrier_receipt_sha256'],
            'samples_ms': samples, 'medians_ms': medians,
            'reduction_percent': (1 - medians['after'] / medians['before']) * 100,
        })
    # Retain diagnostic order and messages for ordinary whitespace/DEL input errors.
    for marker in (' ', '\t', '\n', '\u00a0', '\u2003', '\x1c', '\x7f'):
        value = f'https://github.com/Example{marker}Org/ExampleRepo/issues/1'
        outcomes = []
        for module in (before, after):
            try:
                module._strict_url(value, 'url')
            except module.GrantFoxCarrierCensusInputError as exc:
                outcomes.append(str(exc))
            else:
                raise AssertionError('invalid whitespace accepted')
        assert outcomes[0] == outcomes[1]
    result['diagnostic_parity'] = True
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
