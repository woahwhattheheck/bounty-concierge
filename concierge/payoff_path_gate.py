# SPDX-License-Identifier: MIT
"""Compatibility surface for the payoff-path gate with descriptor-bound file custody.

All payoff semantics remain in :mod:`concierge.payoff_path_gate_core`.  This module
re-exports that API while replacing only file ingress/publication primitives with
retained-descriptor operations.  The split lets the security fix preserve the exact
landed semantic core while closing pathname-generation races in the CLI boundary.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from . import payoff_path_gate_core as _core

PayoffPathError = _core.PayoffPathError
_MAX_INPUT_BYTES = _core._MAX_INPUT_BYTES


def _unsupported() -> PayoffPathError:
    return PayoffPathError(
        "descriptor-bound path custody is unavailable on this platform; refusing unsafe pathname I/O"
    )


def _require_descriptor_support() -> None:
    required = (
        os.name == "posix",
        hasattr(os, "O_DIRECTORY"),
        hasattr(os, "O_NOFOLLOW"),
        os.open in getattr(os, "supports_dir_fd", set()),
        os.stat in getattr(os, "supports_dir_fd", set()),
    )
    if not all(required):
        raise _unsupported()


def _dir_flags() -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _file_read_flags() -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _file_write_flags() -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _path_parts(path: Path) -> tuple[bool, list[str], str]:
    raw = os.fspath(path)
    if type(raw) is not str or not raw or "\x00" in raw:
        raise PayoffPathError(f"invalid filesystem path: {path}")
    candidate = Path(raw)
    name = candidate.name
    if not name or name in {".", ".."}:
        raise PayoffPathError(f"path must name a file: {path}")
    parts = list(candidate.parts)
    if candidate.is_absolute():
        parts = parts[1:]
    if not parts or parts[-1] != name:
        raise PayoffPathError(f"path is not lexically canonical: {path}")
    parents = parts[:-1]
    for component in parents:
        if component in {"", "."}:
            continue
        if component == ".." or os.sep in component or (os.altsep and os.altsep in component):
            raise PayoffPathError(f"parent traversal is not allowed in file path: {path}")
    return candidate.is_absolute(), parents, name


def _open_parent(path: Path) -> tuple[int, str]:
    """Pin every parent component without following symlinks."""

    _require_descriptor_support()
    absolute, parents, name = _path_parts(path)
    anchor = os.sep if absolute else "."
    try:
        current = os.open(anchor, _dir_flags())
    except OSError as exc:
        raise PayoffPathError(f"cannot open path anchor for {path}") from exc
    try:
        for component in parents:
            if component in {"", "."}:
                continue
            try:
                nxt = os.open(component, _dir_flags(), dir_fd=current)
            except OSError as exc:
                raise PayoffPathError(
                    f"output/input parent is not an existing non-symlink directory: {path.parent}"
                ) from exc
            os.close(current)
            current = nxt
        return current, name
    except Exception:
        try:
            os.close(current)
        except OSError:
            pass
        raise


def _generation(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000))),
        int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000))),
    )


def _bounded_read(fd: int, *, limit: int) -> bytes:
    data = bytearray()
    while len(data) <= limit:
        remaining = limit + 1 - len(data)
        chunk = os.read(fd, min(65_536, remaining))
        if not chunk:
            break
        data.extend(chunk)
    if len(data) > limit:
        raise PayoffPathError("input file exceeds byte limit while reading")
    return bytes(data)


def _read_regular(path: Path) -> str:
    parent_fd, name = _open_parent(path)
    fd = -1
    try:
        try:
            fd = os.open(name, _file_read_flags(), dir_fd=parent_fd)
        except OSError as exc:
            raise PayoffPathError(f"cannot open regular non-symlink input: {path}") from exc
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise PayoffPathError(f"input must be a regular non-symlink file: {path}")
        if before.st_size > _MAX_INPUT_BYTES:
            raise PayoffPathError(f"input file too large: {path}")
        data = _bounded_read(fd, limit=_MAX_INPUT_BYTES)
        after = os.fstat(fd)
        if _generation(before) != _generation(after) or len(data) != after.st_size:
            raise PayoffPathError(f"input file changed while being read: {path}")
        try:
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise PayoffPathError(f"input file changed while being read: {path}") from exc
        if (
            not stat.S_ISREG(named.st_mode)
            or named.st_dev != after.st_dev
            or named.st_ino != after.st_ino
        ):
            raise PayoffPathError(f"input file changed while being read: {path}")
        try:
            return data.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise PayoffPathError(f"cannot read UTF-8 input: {path}") from exc
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.close(parent_fd)
        except OSError:
            pass


def _lstat_at(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PayoffPathError(f"cannot inspect output leaf: {name}") from exc


def _preflight_output(path: Path) -> None:
    parent_fd, name = _open_parent(path)
    try:
        if _lstat_at(parent_fd, name) is not None:
            raise PayoffPathError(f"refusing to overwrite existing output: {path}")
    finally:
        os.close(parent_fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        written = os.write(fd, view[offset:])
        if written <= 0:
            raise OSError("short output write")
        offset += written


def _open_output_fd(parent_fd: int, name: str) -> int:
    return os.open(name, _file_write_flags(), 0o600, dir_fd=parent_fd)


def _close_quiet(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def _publish_bundle(outputs: list[tuple[Path, str]]) -> None:
    """Create a bundle against pinned parent generations.

    No destructive rollback is attempted after creation begins.  If a later create,
    write, or fsync fails, already-created outputs remain as truthful partial state.
    This avoids ever pathname-unlinking a generation that another actor may have
    substituted after our create.
    """

    _require_descriptor_support()
    if type(outputs) is not list or not outputs:
        raise PayoffPathError("output bundle must be a non-empty list")

    pinned: list[tuple[Path, bytes, int, str, tuple[int, int, str]]] = []
    file_fds: list[int] = []
    try:
        seen: set[tuple[int, int, str]] = set()
        for raw_path, text in outputs:
            path = Path(raw_path)
            if type(text) is not str:
                raise PayoffPathError(f"output content must be text: {path}")
            data = text.encode("utf-8", errors="strict")
            parent_fd, name = _open_parent(path)
            parent_info = os.fstat(parent_fd)
            key = (int(parent_info.st_dev), int(parent_info.st_ino), name)
            if key in seen:
                os.close(parent_fd)
                raise PayoffPathError("output paths must be distinct within pinned parent generations")
            seen.add(key)
            if _lstat_at(parent_fd, name) is not None:
                os.close(parent_fd)
                raise PayoffPathError(f"refusing to overwrite existing output: {path}")
            pinned.append((path, data, parent_fd, name, key))

        # Do not write any bytes until every destination generation has been pinned
        # and all final names have passed the absence preflight.
        for path, data, parent_fd, name, _ in pinned:
            try:
                fd = _open_output_fd(parent_fd, name)
            except OSError as exc:
                raise PayoffPathError(f"cannot create output exclusively: {path}") from exc
            file_fds.append(fd)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise PayoffPathError(f"created output is not a regular file: {path}")
                _write_all(fd, data)
                os.fsync(fd)
            except PayoffPathError:
                raise
            except OSError as exc:
                raise PayoffPathError(
                    f"output write/durability failure; partial bundle retained: {path}"
                ) from exc

        # Commit directory entry durability only after every file is fully fsynced.
        fsynced: set[tuple[int, int]] = set()
        for path, _, parent_fd, _, _ in pinned:
            info = os.fstat(parent_fd)
            parent_key = (int(info.st_dev), int(info.st_ino))
            if parent_key in fsynced:
                continue
            try:
                os.fsync(parent_fd)
            except OSError as exc:
                raise PayoffPathError(
                    f"output parent durability failure; published files retained: {path.parent}"
                ) from exc
            fsynced.add(parent_key)
    finally:
        for fd in file_fds:
            _close_quiet(fd)
        for _, _, parent_fd, _, _ in pinned:
            _close_quiet(parent_fd)


def _exclusive_write(path: Path, text: str) -> None:
    _publish_bundle([(Path(path), text)])


# Rebind the exact landed semantic core so every existing library/CLI entry point uses
# the descriptor-bound primitives above.
_core._read_regular = _read_regular
_core._preflight_output = _preflight_output
_core._exclusive_write = _exclusive_write
_core._publish_bundle = _publish_bundle

# Preserve the previous module surface, including internal helpers used by existing
# callers/tests, unless this wrapper intentionally overrides the name.
for _name in dir(_core):
    if _name.startswith("__") or _name in {
        "_read_regular",
        "_preflight_output",
        "_exclusive_write",
        "_publish_bundle",
    }:
        continue
    globals().setdefault(_name, getattr(_core, _name))

main = _core.main


if __name__ == "__main__":
    raise SystemExit(main())
