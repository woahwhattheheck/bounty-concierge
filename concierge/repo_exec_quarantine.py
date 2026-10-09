# SPDX-License-Identifier: MIT
"""Offline, non-executing preflight for poisoned ESLint and PostCSS configs.

This is an indicator check, NOT a malware scanner or permission to run a repo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable

from .repo_exec_autoexec import scan_autoexec

SCHEMA = "tjl-repo-exec-quarantine/v1"
HOSTILE_GIT_BLOBS = frozenset({
    "7da565bcb57517fa1c3adc1c824b7e105dae2699",  # observed identical loader cohort
    "0290e72b7db38a21c14e86357e2002d7f00709e3",  # Stellita obfuscated variant
    "b509afc8d03e6ac6b124a00ebe37f3e0e290d367",  # nested/standalone PostCSS loader
    "ea13e03bee28e8a79b9c521179b4abe45c581db2",  # ResumeAI PostCSS variant
    "b20772d40a56f9f2f208d724193bb819d459a2aa",  # PrimeX nested ESLint variant
})
CANDIDATES = frozenset({
    "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs",
    "postcss.config.js", "postcss.config.mjs", "postcss.config.cjs",
})
SKIP_DIRS = frozenset({".git", "node_modules", ".next", ".venv", ".turbo",
                       ".pnpm-store", "coverage", "build", "dist", "target"})
MAX_FILE_BYTES = 2_000_000
MAX_SCAN_DIRS = 4_000
MAX_SCAN_FILES = 300
MAX_DEPTH = 16
FAMILY_MARKER = "GSkqNNyuJw$_padNcYwam"

# Combined behavior, not superficial minification, triggers a *manual review*.
_EVAL = re.compile(r"\beval\s*\(")
_SPAWN = re.compile(r"\b(?:spawn|exec|execSync|spawnSync)\s*\(")
_OBFUSCATED = re.compile(r"(?:[A-Za-z0-9_$]{90,}|[A-Za-z0-9+/=]{180,})")


def git_blob_sha(data: bytes) -> str:
    """Compute the *Git blob* SHA-1, not the ordinary file SHA-1."""
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def scan_repository(root: Path, *, hostile_blobs: Iterable[str] = HOSTILE_GIT_BLOBS) -> dict[str, Any]:
    """Statically inspect nested ESLint/PostCSS configs. Never run JavaScript."""
    bad = frozenset(hostile_blobs)
    results: list[dict[str, Any]] = []
    if root.is_symlink() or not root.is_dir():
        return {"schema": SCHEMA, "disposition": "REVIEW_REQUIRED", "checks": [],
                "reason": "repository root is absent, not a directory, or is a symlink"}

    files_seen = 0
    dirs_seen = 0

    def walk_error(_error: OSError) -> None:
        results.append({"path": "[unreadable-directory]", "status": "REVIEW_REQUIRED",
                        "reason": "directory cannot be enumerated"})

    # Directory metadata is read, but no untrusted JS/module/config is imported.
    # Depth/file/dir bounds prevent untrusted monorepos from exhausting a worker.
    for dirname, subdirs, filenames in os.walk(root, topdown=True, followlinks=False,
                                                onerror=walk_error):
        dirs_seen += 1
        if dirs_seen > MAX_SCAN_DIRS:
            results.append({"path": "[traversal-limit]", "status": "REVIEW_REQUIRED",
                            "reason": "directory scan limit reached; incomplete inspection"})
            break

        directory = Path(dirname)
        relative_directory = directory.relative_to(root)
        if len(relative_directory.parts) >= MAX_DEPTH:
            if any(child not in SKIP_DIRS for child in subdirs):
                results.append({"path": relative_directory.as_posix(), "status": "REVIEW_REQUIRED",
                                "reason": "scan depth limit reached; incomplete inspection"})
            subdirs[:] = []
        else:
            retained = []
            for child in sorted(subdirs):
                if child in SKIP_DIRS:
                    continue
                child_path = directory / child
                if child_path.is_symlink():
                    results.append({"path": child_path.relative_to(root).as_posix(),
                                    "status": "REVIEW_REQUIRED",
                                    "reason": "symlink directory not followed"})
                else:
                    retained.append(child)
            subdirs[:] = retained

        for name in sorted(filenames):
            if name not in CANDIDATES:
                continue
            files_seen += 1
            if files_seen > MAX_SCAN_FILES:
                results.append({"path": "[file-limit]", "status": "REVIEW_REQUIRED",
                                "reason": "configuration scan limit reached; incomplete inspection"})
                break
            path = directory / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                results.append({"path": relative, "status": "REVIEW_REQUIRED",
                                "reason": "symlink not followed"})
                continue
            try:
                if not path.is_file():
                    results.append({"path": relative, "status": "REVIEW_REQUIRED",
                                    "reason": "not a regular file"})
                    continue
                with path.open("rb") as f:
                    raw = f.read(MAX_FILE_BYTES + 1)
            except OSError:
                results.append({"path": relative, "status": "REVIEW_REQUIRED",
                                "reason": "config cannot be read"})
                continue
            if len(raw) > MAX_FILE_BYTES:
                results.append({"path": relative, "status": "REVIEW_REQUIRED",
                                "reason": "config exceeds byte limit"})
                continue

            blob_sha = git_blob_sha(raw)
            check: dict[str, Any] = {"path": relative, "git_blob_sha": blob_sha,
                                     "bytes": len(raw)}
            if blob_sha in bad:
                check["status"] = "KNOWN_HOSTILE_BLOB"
            else:
                try:
                    source = raw.decode("utf-8")
                except UnicodeDecodeError:
                    check["status"] = "REVIEW_REQUIRED"
                    check["reason"] = "non-UTF8 executable configuration"
                else:
                    # This four-signal family is corroborated by multiple
                    # independently inspected provider blobs and a prior incident.
                    family = (FAMILY_MARKER in source and "NONCE_FANOUT" in source
                              and _EVAL.search(source) and _SPAWN.search(source))
                    if family:
                        check["status"] = "KNOWN_HOSTILE_FAMILY"
                    elif _EVAL.search(source) and _SPAWN.search(source) and _OBFUSCATED.search(source):
                        check["status"] = "SUSPICIOUS_EXECUTION_REVIEW"
                    else:
                        check["status"] = "NO_KNOWN_INDICATOR"
            results.append(check)
        if files_seen > MAX_SCAN_FILES:
            break

    # The same user-facing CLI disposition also gates other auto-execution surfaces.
    results.extend(scan_autoexec(root, hostile_blobs=bad))
    statuses = {x["status"] for x in results}
    disposition = ("QUARANTINE" if statuses & {"KNOWN_HOSTILE_BLOB", "KNOWN_HOSTILE_FAMILY"}
                   else "REVIEW_REQUIRED" if statuses & {"REVIEW_REQUIRED", "SUSPICIOUS_EXECUTION_REVIEW"}
                   else "NO_KNOWN_INDICATOR")
    return {
        "schema": SCHEMA, "disposition": disposition, "checks": results,
        "scope": "Nested ESLint/PostCSS, npm lifecycle hooks, VS Code folderOpen tasks and public/fonts WOFF2 magic checked offline under bounds. No clean/safe-to-execute assertion; other execution vectors are not cleared.",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repo", type=Path, help="Existing local checkout, never a URL")
    p.add_argument("--json", action="store_true", help="Machine-readable receipt")
    args = p.parse_args(argv)
    result = scan_repository(args.repo)
    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        print(result["disposition"])
        for item in result["checks"]:
            print(f"  {item['path']}: {item['status']} {item.get('git_blob_sha', '')}")
        print(result.get("scope", result.get("reason", "")))
    return 2 if result["disposition"] == "QUARANTINE" else 3 if result["disposition"] == "REVIEW_REQUIRED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
