# SPDX-License-Identifier: MIT
"""Descriptor-bound reader for one payoff-path claim bundle.

The claim boundary consumes a fixed four-file evidence bundle and, for chained
continuity generations, may also consume ``previous-receipt.json``.  File names
are not caller-controlled.  Every path component and member is opened relative
to retained directory descriptors without following symlinks.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

BUNDLE_FILENAMES = {
    "document": "work.json",
    "packet": "packet.json",
    "markdown": "review.md",
    "receipt": "receipt.json",
}
PREVIOUS_RECEIPT_FILENAME = "previous-receipt.json"
MAX_INPUT_BYTES = 4_000_000


class PayoffBundleError(ValueError):
    """Safe failure raised before payoff bundle bytes enter semantic verification."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _dir_flags() -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    return flags | getattr(os, "O_CLOEXEC", 0)


def _file_flags() -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    return flags | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)


def _require_support() -> None:
    if not (
        os.name == "posix"
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in getattr(os, "supports_dir_fd", set())
    ):
        raise PayoffBundleError(
            "UNSAFE_BUNDLE_PLATFORM",
            "descriptor-bound payoff bundle custody is unavailable",
        )


def _open_directory(bundle: str | os.PathLike[str]) -> int:
    if isinstance(bundle, os.PathLike):
        raw = os.fspath(bundle)
    elif type(bundle) is str:
        raw = bundle
    else:
        raw = ""
    if type(raw) is not str or not raw or raw != raw.strip() or "\x00" in raw:
        raise PayoffBundleError(
            "BUNDLE_REQUIRED",
            "live claim requires a payoff bundle directory",
        )

    _require_support()
    candidate = Path(raw)
    parts = list(candidate.parts)
    absolute = candidate.is_absolute()
    if absolute:
        parts = parts[1:]
    if any(
        part == ".." or os.sep in part or (os.altsep and os.altsep in part)
        for part in parts
    ):
        raise PayoffBundleError(
            "UNSAFE_BUNDLE_DIRECTORY",
            "payoff bundle path must not contain traversal components",
        )

    try:
        current = os.open(os.sep if absolute else ".", _dir_flags())
    except OSError as exc:
        raise PayoffBundleError(
            "BUNDLE_UNAVAILABLE",
            "payoff bundle directory is unavailable",
        ) from exc
    try:
        for component in parts:
            if component in {"", "."}:
                continue
            try:
                nxt = os.open(component, _dir_flags(), dir_fd=current)
            except OSError as exc:
                raise PayoffBundleError(
                    "UNSAFE_BUNDLE_DIRECTORY",
                    "payoff bundle must be an existing all-components non-symlink directory",
                ) from exc
            os.close(current)
            current = nxt
        if not stat.S_ISDIR(os.fstat(current).st_mode):
            raise PayoffBundleError(
                "UNSAFE_BUNDLE_DIRECTORY",
                "payoff bundle must be a directory",
            )
        return current
    except Exception:
        os.close(current)
        raise


def _generation(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000))),
        int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000))),
    )


def _read_open_member(fd: int, name: str) -> str:
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise PayoffBundleError(
                "UNSAFE_BUNDLE_FILE",
                f"payoff bundle member {name} must be a regular non-symlink file",
            )
        if before.st_size > MAX_INPUT_BYTES:
            raise PayoffBundleError(
                "BUNDLE_FILE_TOO_LARGE",
                f"payoff bundle member {name} exceeds the size limit",
            )

        chunks: list[bytes] = []
        total = 0
        while total <= MAX_INPUT_BYTES:
            chunk = os.read(fd, min(65_536, MAX_INPUT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > MAX_INPUT_BYTES:
            raise PayoffBundleError(
                "BUNDLE_FILE_TOO_LARGE",
                f"payoff bundle member {name} exceeds the size limit",
            )
        after = os.fstat(fd)
        if _generation(before) != _generation(after) or total != after.st_size:
            raise PayoffBundleError(
                "BUNDLE_MEMBER_CHANGED",
                f"payoff bundle member {name} changed while being read",
            )
        try:
            return b"".join(chunks).decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise PayoffBundleError(
                "INVALID_BUNDLE_ENCODING",
                f"payoff bundle member {name} must be UTF-8",
            ) from exc
    finally:
        os.close(fd)


def _read_member(directory_fd: int, name: str) -> str:
    try:
        fd = os.open(name, _file_flags(), dir_fd=directory_fd)
    except OSError as exc:
        raise PayoffBundleError(
            "BUNDLE_MEMBER_UNAVAILABLE",
            f"payoff bundle member {name} is unavailable",
        ) from exc
    return _read_open_member(fd, name)


def _read_optional_member(directory_fd: int, name: str) -> str | None:
    try:
        fd = os.open(name, _file_flags(), dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PayoffBundleError(
            "BUNDLE_MEMBER_UNAVAILABLE",
            f"optional payoff bundle member {name} could not be opened safely",
        ) from exc
    return _read_open_member(fd, name)


def read_payoff_bundle(bundle: str | os.PathLike[str]) -> dict[str, str | None]:
    """Read fixed-name members from one pinned all-components directory FD.

    ``previous_receipt`` is absent only when the fixed predecessor file does not
    exist.  Any other open/read failure remains fail-closed.  Whether a predecessor
    is semantically required is deliberately delegated to the canonical payoff
    verifier so bundle custody cannot invent continuity policy.
    """

    directory_fd = _open_directory(bundle)
    try:
        result: dict[str, str | None] = {
            key: _read_member(directory_fd, filename)
            for key, filename in BUNDLE_FILENAMES.items()
        }
        result["previous_receipt"] = _read_optional_member(
            directory_fd, PREVIOUS_RECEIPT_FILENAME
        )
        return result
    finally:
        os.close(directory_fd)
