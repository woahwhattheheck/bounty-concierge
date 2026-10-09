# SPDX-License-Identifier: MIT
"""Bounded, read-only detection of other automatic-execution source surfaces.

No source from the inspected checkout is imported, invoked, or decoded as code.
Signals demand human review; they do not establish an author's intent.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable

SKIP_DIRS = frozenset({".git", "node_modules", ".next", ".venv", ".turbo",
                       ".pnpm-store", "coverage", "build", "dist", "target"})
MAX_SCAN_DIRS = 4_000
MAX_SCAN_FILES = 300
MAX_DEPTH = 16
MAX_FILE_BYTES = 2_000_000
AUTO_HOOKS = frozenset({"preinstall", "install", "postinstall", "prepare",
                        "prepack", "postpack", "prepublish", "prepublishOnly",
                        "postpublish", "preuninstall", "uninstall", "postuninstall"})
FAMILY_MARKER = "GSkqNNyuJw$_padNcYwam"
_FAMILY_EVAL = re.compile(r"\beval\s*\(")
_FAMILY_SPAWN = re.compile(r"\b(?:spawn|exec|execSync|spawnSync)\s*\(")
_FOLD_OPEN = re.compile(rb'"runOn"\s*:\s*"folderOpen"')


def _git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _auto_task(node: Any) -> bool:
    if isinstance(node, dict):
        return any((k == "runOn" and v == "folderOpen") or _auto_task(v)
                   for k, v in node.items())
    if isinstance(node, list):
        return any(_auto_task(v) for v in node)
    return False


def _kind(directory: Path, name: str) -> str | None:
    if name == "package.json":
        return "npm_lifecycle"
    if name == "tasks.json" and directory.name == ".vscode":
        return "vscode_folder_open"
    if name.lower().endswith(".woff2") and directory.name == "fonts" and directory.parent.name == "public":
        return "woff2_disguise"
    return None


def _inspect(path: Path, relative: str, kind: str, bad: frozenset[str]) -> dict[str, Any]:
    check: dict[str, Any] = {"path": relative, "surface": kind}
    if path.is_symlink():
        return {**check, "status": "REVIEW_REQUIRED", "reason": "symlink not followed"}
    # O_NOFOLLOW protects the final component even if it changes after is_symlink().
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return {**check, "status": "REVIEW_REQUIRED", "reason": "not a regular file"}
            raw = handle.read(MAX_FILE_BYTES + 1)
    except OSError:
        return {**check, "status": "REVIEW_REQUIRED", "reason": "source cannot be read without following links"}
    if len(raw) > MAX_FILE_BYTES:
        return {**check, "status": "REVIEW_REQUIRED", "reason": "source exceeds byte limit"}

    blob = _git_blob_sha(raw)
    check.update({"bytes": len(raw), "git_blob_sha": blob})
    if blob in bad:
        return {**check, "status": "KNOWN_HOSTILE_BLOB"}
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError:
        if kind == "woff2_disguise" and raw.startswith(b"wOF2"):
            return {**check, "status": "NO_KNOWN_INDICATOR"}
        return {**check, "status": "REVIEW_REQUIRED", "reason": "non-UTF8 or unexpected source bytes"}

    if (FAMILY_MARKER in source and "NONCE_FANOUT" in source
            and _FAMILY_EVAL.search(source) and _FAMILY_SPAWN.search(source)):
        return {**check, "status": "KNOWN_HOSTILE_FAMILY"}
    if kind == "woff2_disguise":
        return ({**check, "status": "NO_KNOWN_INDICATOR"} if raw.startswith(b"wOF2")
                else {**check, "status": "REVIEW_REQUIRED", "reason": "woff2 magic absent; inspect disguised source"})

    try:
        parsed = json.loads(source)
    except (ValueError, TypeError, RecursionError):
        if kind == "vscode_folder_open" and _FOLD_OPEN.search(raw):
            return {**check, "status": "REVIEW_REQUIRED", "reason": "folderOpen task in JSONC or malformed task file"}
        return {**check, "status": "REVIEW_REQUIRED", "reason": "JSON not strictly parseable; automatic execution unknown"}
    if not isinstance(parsed, dict):
        return {**check, "status": "REVIEW_REQUIRED", "reason": "unexpected JSON root"}
    if kind == "vscode_folder_open":
        try:
            found = _auto_task(parsed)
        except RecursionError:
            return {**check, "status": "REVIEW_REQUIRED", "reason": "task JSON too deeply nested"}
        return ({**check, "status": "REVIEW_REQUIRED", "reason": "VS Code folderOpen task can execute on folder open"}
                if found else {**check, "status": "NO_KNOWN_INDICATOR"})

    scripts = parsed.get("scripts", {})
    if not isinstance(scripts, dict):
        return {**check, "status": "REVIEW_REQUIRED", "reason": "npm scripts are not a map"}
    hooks = sorted(AUTO_HOOKS.intersection(scripts))
    if hooks:
        return {**check, "status": "REVIEW_REQUIRED", "hooks": hooks,
                "reason": "npm lifecycle hooks can execute other files; inspect every target before running npm"}
    return {**check, "status": "NO_KNOWN_INDICATOR"}


def scan_autoexec(root: Path, *, hostile_blobs: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Inspect npm hooks, .vscode tasks and public/fonts WOFF2 bytes; never run them."""
    results: list[dict[str, Any]] = []
    if root.is_symlink() or not root.is_dir():
        return [{"path": "[root]", "status": "REVIEW_REQUIRED", "reason": "invalid or symlinked root"}]
    bad = frozenset(hostile_blobs)
    dirs_seen = files_seen = 0

    def unreadable(_: OSError) -> None:
        results.append({"path": "[unreadable-directory]", "status": "REVIEW_REQUIRED",
                        "reason": "directory cannot be enumerated"})

    for directory_name, subdirs, filenames in os.walk(root, topdown=True, followlinks=False, onerror=unreadable):
        dirs_seen += 1
        if dirs_seen > MAX_SCAN_DIRS:
            results.append({"path": "[autoexec-dir-limit]", "status": "REVIEW_REQUIRED",
                            "reason": "automatic-execution scan directory limit reached"})
            break
        directory = Path(directory_name)
        relative_directory = directory.relative_to(root)
        if len(relative_directory.parts) >= MAX_DEPTH:
            if any(child not in SKIP_DIRS for child in subdirs):
                results.append({"path": relative_directory.as_posix(), "status": "REVIEW_REQUIRED",
                                "reason": "automatic-execution scan depth limit reached"})
            subdirs[:] = []
        else:
            kept = []
            for child in sorted(subdirs):
                if child in SKIP_DIRS:
                    continue
                subpath = directory / child
                if subpath.is_symlink():
                    results.append({"path": subpath.relative_to(root).as_posix(),
                                    "status": "REVIEW_REQUIRED", "reason": "symlink directory not followed"})
                else:
                    kept.append(child)
            subdirs[:] = kept
        for name in sorted(filenames):
            kind = _kind(directory, name)
            if kind is None:
                continue
            files_seen += 1
            if files_seen > MAX_SCAN_FILES:
                results.append({"path": "[autoexec-file-limit]", "status": "REVIEW_REQUIRED",
                                "reason": "automatic-execution scan file limit reached"})
                break
            path = directory / name
            results.append(_inspect(path, path.relative_to(root).as_posix(), kind, bad))
        if files_seen > MAX_SCAN_FILES:
            break
    return results
