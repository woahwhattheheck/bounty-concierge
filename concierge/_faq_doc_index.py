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


def _remember(key, generation, rows, size):
    global _CACHE_BYTES
    with _LOCK:
        old = _CACHE.pop(key, None)
        if old is not None:
            _CACHE_BYTES -= old[2]
        while _CACHE and (len(_CACHE) >= _MAX_ENTRIES
                          or _CACHE_BYTES + size > _MAX_BYTES):
            _, discarded = _CACHE.popitem(last=False)
            _CACHE_BYTES -= discarded[2]
        _CACHE[key] = (generation, tuple(rows), size)
        _CACHE_BYTES += size


def iter_paragraphs(directory, tokenize):
    """Yield (paragraph, tokens), reloading changed, added or removed Markdown.

    ``tokenize`` supplies the caller's matching semantics. Only complete reads of
    an unchanged generation are retained. Oversized corpora continue streaming
    without caching; unreadable files retain the reader's skip behavior.
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

    # Include generation/key containers and conservative cache bookkeeping, not
    # merely input-file lengths: token sets can outweigh the Markdown itself.
    size = _size(generation) + _size(directory) + sys.getsizeof(()) + 1024
    retained = [] if generation is not None and size <= _MAX_BYTES else None
    for name in names:
        try:
            with open(os.path.join(directory, name), "r", encoding="utf-8",
                      errors="replace") as stream:
                content = stream.read()
        except OSError:
            retained = None
            continue
        for paragraph in re.split(r"\n\s*\n", content):
            paragraph = paragraph.strip()
            if len(paragraph) < 20:
                continue
            row = (paragraph, frozenset(tokenize(paragraph)))
            if retained is not None:
                size += _size(row) + _POINTER_BYTES
                if size > _MAX_BYTES:
                    retained = None
                else:
                    retained.append(row)
            yield row

    if retained is not None:
        try:
            unchanged = _generation(directory)[1] == generation
        except OSError:
            unchanged = False
        if unchanged:
            _remember(key, generation, retained, size)
