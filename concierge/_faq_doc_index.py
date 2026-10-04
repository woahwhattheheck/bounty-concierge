# SPDX-License-Identifier: MIT
"""Reuse bounded paragraph tokens while the local Markdown corpus is unchanged."""

from collections import OrderedDict
import os
import re
import sys
from threading import Lock


_MAX_ENTRIES = 4
_MAX_BYTES = 8 * 1024 * 1024
_POINTER_BYTES = sys.getsizeof((None,)) - sys.getsizeof(())
_CACHE = OrderedDict()
_CACHE_BYTES = 0
_LOCK = Lock()


def _size(value):
    """Count retained containers and values, conservatively counting shared ones."""
    total = sys.getsizeof(value)
    if isinstance(value, (tuple, frozenset)):
        total += sum(_size(item) for item in value)
    return total


def _generation(directory):
    # Keep the reader's existing filesystem order: ties use the first paragraph.
    names = tuple(name for name in os.listdir(directory) if name.endswith(".md"))
    markers = []
    for name in names:
        try:
            info = os.stat(os.path.join(directory, name))
        except OSError:
            return names, None
        markers.append((name, info.st_dev, info.st_ino, info.st_mode,
                        info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    return names, tuple(markers)


def _remember(key, generation, rows, size, file_spans):
    global _CACHE_BYTES
    with _LOCK:
        old = _CACHE.pop(key, None)
        if old is not None:
            _CACHE_BYTES -= old[2]
        while _CACHE and (len(_CACHE) >= _MAX_ENTRIES
                          or _CACHE_BYTES + size > _MAX_BYTES):
            _, discarded = _CACHE.popitem(last=False)
            _CACHE_BYTES -= discarded[2]
        _CACHE[key] = (generation, tuple(rows), size, tuple(file_spans))
        _CACHE_BYTES += size


def _paragraphs(content):
    """Split lazily without retaining a second complete copy of the corpus."""
    start = 0
    for separator in re.finditer(r"\n\s*\n", content):
        yield content[start:separator.start()]
        start = separator.end()
    yield content[start:]


def iter_paragraphs(directory, tokenize):
    """Yield (paragraph, tokens), reloading changed, added or removed Markdown.

    ``tokenize`` supplies the caller's matching semantics. Only complete reads of
    an unchanged generation are retained. Oversized corpora continue streaming
    without caching; unreadable files retain the reader's skip behavior.
    After a corpus change, unchanged files reuse their previous rows in the
    current filesystem order. Only new or changed files are tokenized again.
    """
    directory = os.path.realpath(directory)
    names, generation = _generation(directory)
    key = (directory, tokenize)
    with _LOCK:
        cached = _CACHE.get(key)
        if generation is not None and cached is not None and cached[0] == generation:
            _CACHE.move_to_end(key)
            rows = cached[1]
        else:
            rows = None
    if rows is not None:
        yield from rows
        return

    # Reuse complete per-file row spans from the previous generation. Marker
    # equality includes filesystem identity/metadata; changed files are rebuilt.
    reusable = {}
    if generation is not None and cached is not None:
        start = 0
        for marker, (end, row_bytes) in zip(cached[0], cached[3]):
            reusable[marker] = (start, end, row_bytes)
            start = end

    # Include generation/key containers plus retained span metadata in the same
    # bounded byte budget; token sets can outweigh the Markdown itself.
    size = (_size(generation) + _size(directory) + 2 * sys.getsizeof(()) + 1024
            + len(names) * (sys.getsizeof((0, 0)) + 2 * sys.getsizeof(0)
                            + _POINTER_BYTES))
    retained = [] if generation is not None and size <= _MAX_BYTES else None
    file_spans = []
    for index, name in enumerate(names):
        span = reusable.get(generation[index]) if generation is not None else None
        row_bytes = 0
        if span is not None:
            row_bytes = span[2]
            if retained is not None:
                size += row_bytes
                if size > _MAX_BYTES:
                    retained = None
            file_rows = (cached[1][pos] for pos in range(span[0], span[1]))
        else:
            try:
                with open(os.path.join(directory, name), "r", encoding="utf-8",
                          errors="replace") as stream:
                    content = stream.read()
            except OSError:
                retained = None
                continue
            file_rows = (
                (paragraph, frozenset(tokenize(paragraph)))
                for paragraph in (part.strip() for part in _paragraphs(content))
                if len(paragraph) >= 20
            )

        for row in file_rows:
            if retained is not None:
                if span is None:
                    row_size = _size(row) + _POINTER_BYTES
                    row_bytes += row_size
                    size += row_size
                if size > _MAX_BYTES:
                    retained = None
                else:
                    retained.append(row)
            yield row
        if retained is not None:
            file_spans.append((len(retained), row_bytes))

    if retained is not None:
        try:
            unchanged = _generation(directory)[1] == generation
        except OSError:
            unchanged = False
        if unchanged:
            _remember(key, generation, retained, size, file_spans)
