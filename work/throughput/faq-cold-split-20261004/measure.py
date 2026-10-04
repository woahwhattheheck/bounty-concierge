"""Source-bound local paragraph-index measurement; no network or provider calls."""
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import statistics
import sys
import tempfile
import time
import tracemalloc


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tokens(value):
    return re.findall(r"\w+", value.lower())


def consume(module, directory, limit=None):
    digest = hashlib.sha256()
    count = 0
    iterator = module.iter_paragraphs(directory, tokens)
    try:
        for paragraph, words in iterator:
            digest.update(json.dumps((paragraph, sorted(words)), ensure_ascii=False).encode())
            count += 1
            if limit is not None and count >= limit:
                break
    finally:
        iterator.close()
    return count, digest.hexdigest()


def clear(module):
    module._CACHE.clear()
    module._CACHE_BYTES = 0


def main():
    before_path, after_path = map(Path, sys.argv[1:3])
    modules = {"before": load("before", before_path), "after": load("after", after_path)}
    output = {"python": sys.version, "sources": {}, "compatibility": {}, "workloads": {}}
    for name, path in (("before", before_path), ("after", after_path)):
        raw = path.read_bytes()
        output["sources"][name] = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        # Exact separator compatibility, including CRLF, Unicode whitespace,
        # trailing separators, empty fragments and short filtered paragraphs.
        paragraph = "A sufficiently long paragraph with repeated alpha alpha words."
        cases = ["", paragraph, "\n\n" + paragraph + "\n\n", "short\n\n" + paragraph]
        cases += [paragraph + separator + paragraph.upper() for separator in ("\n\n", "\n \n", "\r\n\r\n", "\n\t\n", "\n\u2003\n", "\n\n\n", "\n\n \n")]
        for number, text in enumerate(cases):
            (directory / "input.md").write_text(text)
            results = []
            for module in modules.values():
                clear(module)
                cold = consume(module, directory)
                warm = consume(module, directory)
                assert cold == warm
                results.append(cold)
            assert results[0] == results[1], number
        output["compatibility"]["separator_and_warm_cache_cases"] = len(cases)
        # Same retained key must reload changed/additional/deleted Markdown.
        results = {name: [] for name in modules}
        for step in ("change", "add", "delete"):
            if step == "change":
                (directory / "input.md").write_text(paragraph + " changed content")
            elif step == "add":
                (directory / "second.md").write_text(paragraph.upper())
            else:
                (directory / "input.md").unlink()
            for name, module in modules.items():
                results[name].append(consume(module, directory))
        assert results["before"] == results["after"]
        output["compatibility"]["generation_transitions"] = 3
        (directory / "second.md").unlink()
        for name, rows, limit in (("small_cold", 100, None), ("oversized_complete", 100000, None), ("oversized_first_row", 100000, 1)):
            text = "\n\n".join(f"{index:06} {paragraph}" for index in range(rows))
            (directory / "input.md").write_text(text)
            byte_count = len(text.encode()); del text
            samples = {key: [] for key in modules}
            peaks, digests = {}, {}
            for pair in range(3):
                for key in (("before", "after") if pair % 2 == 0 else ("after", "before")):
                    module = modules[key]; clear(module); gc.collect()
                    start = time.perf_counter_ns()
                    result = consume(module, directory, limit)
                    elapsed = (time.perf_counter_ns() - start) / 1e6
                    samples[key].append(elapsed); digests[key] = result
            assert digests["before"] == digests["after"]
            for key, module in modules.items():
                clear(module); gc.collect(); tracemalloc.start()
                result = consume(module, directory, limit)
                _, peaks[key] = tracemalloc.get_traced_memory(); tracemalloc.stop()
                assert result == digests[key]
                if limit is not None: assert not module._CACHE
            output["workloads"][name] = {"markdown_bytes": byte_count, "result": digests["before"], "elapsed_ms": samples, "median_ms": {key: statistics.median(values) for key, values in samples.items()}, "peak_traced_bytes": peaks}
    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    main()
