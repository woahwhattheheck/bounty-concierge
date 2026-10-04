#!/usr/bin/env python3
"""Offline comparison of two complete source-bundle unpacker files (Python 3.12+)."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import platform
import shutil
import statistics
import tarfile
import tempfile
import time
import zipfile


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blob(path):
    data = path.read_bytes()
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def outcome(function, value):
    try:
        return ('ok', function(value))
    except Exception as error:
        return (type(error).__name__, str(error))


def fixture(path, count):
    compressed = io.BytesIO()
    with tarfile.open(fileobj=compressed, mode='w:gz') as archive:
        root = tarfile.TarInfo('source'); root.type = tarfile.DIRTYPE; root.mode = 0o755
        archive.addfile(root)
        for index in range(count):
            payload = f'fixture {index}\n'.encode()
            info = tarfile.TarInfo(f'source/pkg/mod_{index // 100:03}/file_{index:05}.txt')
            info.size = len(payload); info.mode = 0o644
            archive.addfile(info, io.BytesIO(payload))
        link = tarfile.TarInfo('source/pkg/mod_000/alias.txt')
        link.type = tarfile.SYMTYPE; link.linkname = 'file_00000.txt'; link.mode = 0o777
        archive.addfile(link)
    data = compressed.getvalue()
    manifest = {'schema': 'github-source-bundle/v1', 'repository': 'fixture/source',
                'commit_sha': '1' * 40, 'tree_sha': '2' * 40,
                'archive': {'file': 'source.tar.gz', 'format': 'git-archive/tar.gz',
                            'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}}
    with zipfile.ZipFile(path, 'w') as bundle:
        bundle.writestr('manifest.json', json.dumps(manifest))
        bundle.writestr('source.tar.gz', data)
    return manifest


def snapshot(root):
    result = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = ['symlink', str(path.readlink())]
        elif path.is_file():
            result[relative] = ['file', hashlib.sha256(path.read_bytes()).hexdigest()]
        else:
            result[relative] = ['directory']
    return result


def paired(functions, cleanup, pairs=7):
    samples = {'before': [], 'after': []}
    for function in functions:
        function(); cleanup()
    for pair in range(pairs):
        for index in ([0, 1] if pair % 2 == 0 else [1, 0]):
            start = time.perf_counter_ns(); functions[index]()
            samples[('before', 'after')[index]].append((time.perf_counter_ns() - start) / 1e6)
            cleanup()
    medians = {name: statistics.median(values) for name, values in samples.items()}
    return {'milliseconds': samples, 'median_ms': medians,
            'median_ratio_before_over_after': medians['before'] / medians['after']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path); parser.add_argument('after', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    modules = [load(args.before, 'before_unpacker'), load(args.after, 'after_unpacker')]
    canonical = ['source', 'src/main.py', '.gitignore', 'docs/v1.2.md',
                 'pkg/a b.txt', 'pkg/日本語.txt', 'pkg/.../data', 'C:relative/file']
    normalized = ['./src/main.py', 'src//main.py', 'src/./main.py', 'src/',
                  'src///', './.gitignore', 'a/./b//c/', './pkg/日本語.txt']
    invalid = ['', '.', './', '/src', 'a/../b', '../a', 'a\\b', 'a\0b', None, 42]
    groups = []
    for label, values in [('canonical paths', canonical), ('normalization fallback', normalized),
                          ('rejected inputs', invalid)]:
        assert [outcome(modules[0]._path, x) for x in values] == [outcome(modules[1]._path, x) for x in values]
        groups.append(label)
    results = {'timestamp_utc': datetime.now(timezone.utc).isoformat(),
               'python': platform.python_version(), 'platform': platform.platform(),
               'before_blob': blob(args.before), 'after_blob': blob(args.after),
               'method': 'One warmup per variant; seven alternating paired samples; cleanup untimed.',
               'scope': 'Synthetic local fixture; complete imported production modules; no network or provider calls.'}
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); archive = root / 'fixture.zip'
        results['fixture'] = fixture(archive, 8000)
        selectors = ['pkg/mod_000', 'pkg/mod_000/file_00000.txt',
                     'pkg/mod_040/file_04000.txt', 'pkg/mod_079/file_07999.txt']
        destination = root / 'output'
        def cleanup():
            if destination.exists(): shutil.rmtree(destination)
        def unpack(module, paths=selectors):
            return module.unpack_bundle(archive, destination, repository='fixture/source',
                                        commit='1' * 40, paths=paths, temp_directory=root)
        reference = None
        for index, module in enumerate(modules):
            receipt = unpack(module, ['./pkg/mod_000//', *selectors[1:]])
            receipt.pop('source_root')
            state = (receipt, snapshot(destination))
            if index == 0: reference = state
            else: assert state == reference
            cleanup()
        groups.append('selected extraction, normalization, overlap, bytes and symlink parity')
        results['extraction_receipt'] = reference[0]
        for module in modules:
            try: unpack(module, ['absent/path'])
            except ValueError as error: assert str(error) == 'selected paths are absent: absent/path'
            else: raise AssertionError('missing selector accepted')
            assert not destination.exists()
        groups.append('missing selector cleanup')
        destination.mkdir(); (destination / 'keep').write_text('unchanged')
        for module in modules:
            try: unpack(module)
            except FileExistsError: pass
            else: raise AssertionError('existing destination accepted')
            assert (destination / 'keep').read_text() == 'unchanged'
        cleanup(); groups.append('existing destination preserved')
        names = [f'source/pkg/mod_{i // 100:03}/file_{i:05}.txt' for i in range(10000)]
        fallback = [f'./source//pkg/mod_{i // 100:03}/./file_{i:05}.txt/' for i in range(10000)]
        def path_functions(values):
            return [lambda module=module: [module._path(name) for name in values] for module in modules]
        results['canonical_10000_paths'] = paired(path_functions(names), lambda: None)
        results['fallback_10000_paths'] = paired(path_functions(fallback), lambda: None)
        results['selected_unpack_8000_files'] = paired([lambda module=module: unpack(module) for module in modules], cleanup)
    results['compatibility_groups_passed'] = groups
    args.output.write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps({key: value.get('median_ms') for key, value in results.items()
                      if isinstance(value, dict) and 'median_ms' in value}, indent=2))
    print('Compatibility groups passed:', len(groups))


if __name__ == '__main__':
    main()
