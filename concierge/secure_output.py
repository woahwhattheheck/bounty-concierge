# SPDX-License-Identifier: MIT
"""Race-resistant create-exclusive output helpers.

The helpers in this module deliberately fail closed when the host cannot offer
POSIX-style directory-descriptor traversal with ``O_NOFOLLOW``.  A leaf-only
``O_NOFOLLOW`` is insufficient because ``open('/a/link/file', ...)`` still
follows a symlink in ``/a/link``.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import Tuple


class SecureOutputError(RuntimeError):
    """The requested path cannot be created under no-follow semantics."""


def _directory_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory or os.open not in getattr(os, "supports_dir_fd", set()):
        raise SecureOutputError(
            "secure output creation requires O_NOFOLLOW/O_DIRECTORY and dir_fd support"
        )
    return os.O_RDONLY | nofollow | directory | getattr(os, "O_CLOEXEC", 0)


def open_verified_parent(path: Path) -> Tuple[int, str]:
    """Open ``path.parent`` without following any non-root path component.

    Returns an owned directory descriptor plus the leaf name.  The caller must
    close the descriptor.  ``..`` is rejected so the authority root cannot move
    while traversing a relative path.
    """
    path = Path(path)
    leaf = path.name
    if not leaf or leaf in {".", ".."}:
        raise SecureOutputError("output path must name a leaf file")

    flags = _directory_flags()
    parent = path.parent
    if path.is_absolute():
        anchor = path.anchor
        if not anchor:
            raise SecureOutputError("absolute output path has no filesystem anchor")
        current = os.open(anchor, flags)
        parts = parent.parts[1:]
    else:
        current = os.open(".", flags)
        parts = parent.parts

    try:
        for part in parts:
            if part in {"", "."}:
                continue
            if part == "..":
                raise SecureOutputError("output parent traversal with '..' is not allowed")
            try:
                nxt = os.open(part, flags, dir_fd=current)
            except OSError as exc:
                raise SecureOutputError(
                    f"refusing symlinked or non-directory output parent component: {part}"
                ) from exc
            try:
                info = os.fstat(nxt)
                if not stat.S_ISDIR(info.st_mode):
                    raise SecureOutputError(
                        f"output parent component is not a directory: {part}"
                    )
            except Exception:
                os.close(nxt)
                raise
            os.close(current)
            current = nxt
        return current, leaf
    except Exception:
        os.close(current)
        raise


def create_exclusive_regular(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    """Create one regular file without following the leaf or any parent symlink."""
    if type(payload) is not bytes:
        raise SecureOutputError("output payload must be bytes")

    parent_fd, leaf = open_verified_parent(path)
    fd = -1
    created = False
    try:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            fd = os.open(leaf, flags, mode, dir_fd=parent_fd)
            created = True
        except OSError as exc:
            raise SecureOutputError(
                f"refusing to overwrite or follow output path: {path}"
            ) from exc

        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SecureOutputError("output must be a regular file")

        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise SecureOutputError("short output write")
            view = view[written:]
        os.fsync(fd)
    except Exception:
        if fd >= 0:
            os.close(fd)
            fd = -1
        if created:
            try:
                os.unlink(leaf, dir_fd=parent_fd)
            except OSError:
                pass
        raise
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(parent_fd)
