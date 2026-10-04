"""Exercise the real publisher with controlled collector responses; no network."""
import importlib.util
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import tempfile
import types

SOURCE = Path(os.environ.get('PUBLISHER_SOURCE',
    str(Path(__file__).resolve().parents[3] / 'concierge' / 'bounty_index_publish.py')))


def load_publisher():
    package = types.ModuleType('concierge')
    collector = types.ModuleType('concierge.bounty_index')
    collector.BountyFetchIncompleteError = RuntimeError
    package.bounty_index = collector
    sys.modules['concierge'] = package
    sys.modules['concierge.bounty_index'] = collector
    spec = importlib.util.spec_from_file_location('publisher_under_test', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, collector


def report(label):
    return {
        'started_at': f'2026-10-04T08:{"00" if label == "old" else "01"}:00+00:00',
        'updated_at': f'2026-10-04T08:{"03" if label == "old" else "02"}:00+00:00',
        'complete': True, 'rate_limited': False,
        'retry_after_seconds': None, 'rate_limit_reset_at': None,
        'repositories': [{'repo': 'Example/Repo', 'status': 'COMPLETE',
                          'pages_fetched': 1, 'bounty_count': 1, 'http_status': 200}],
        'bounties': [{'repo': 'Example/Repo', 'number': 1,
                     'url': 'https://github.com/example/repo/issues/1',
                     'title': label, 'reward_rtc': 50}], 'total_count': 1,
    }


def write(label, output, waiting, newer_done, receipts):
    module, collector = load_publisher()
    calls = 0
    def fetch(**kwargs):
        nonlocal calls
        calls += 1
        if label == 'old':
            waiting.set()
            if not newer_done.wait(10):
                raise RuntimeError('newer publisher never finished')
        return report(label)
    collector.fetch_bounties_report = fetch
    try:
        module.write_index_atomic(output)
        receipts.put({'label': label, 'outcome': 'published', 'collector_calls': calls})
    except Exception as exc:
        receipts.put({'label': label, 'outcome': type(exc).__name__, 'collector_calls': calls})
    finally:
        if label == 'new':
            newer_done.set()


def main():
    ctx = mp.get_context('spawn')
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / 'index.json'
        waiting, newer_done, receipts = ctx.Event(), ctx.Event(), ctx.Queue()
        old = ctx.Process(target=write, args=('old', output, waiting, newer_done, receipts))
        new = ctx.Process(target=write, args=('new', output, waiting, newer_done, receipts))
        old.start()
        assert waiting.wait(10), 'older collector did not enter'
        new.start()
        for worker in (new, old):
            worker.join(15)
            assert not worker.is_alive() and worker.exitcode == 0, worker.exitcode
        rows = [receipts.get(timeout=2), receipts.get(timeout=2)]
        final = json.loads(output.read_text())
        result = {'workers': sorted(rows, key=lambda row: row['label']),
                  'final_title': final['bounties'][0]['title'],
                  'final_started_at': final['started_at'],
                  'temporary_residue': [p.name for p in Path(directory).glob('*.tmp')]}
        print(json.dumps(result, indent=2))
        assert sum(row['collector_calls'] for row in rows) == 2
        assert not result['temporary_residue']
        outcomes = {row['label']: row['outcome'] for row in rows}
        expected = {'new': 'published', 'old': 'BountyIndexSupersededError'}
        return 0 if result['final_title'] == 'new' and outcomes == expected else 1


if __name__ == '__main__':
    raise SystemExit(main())
