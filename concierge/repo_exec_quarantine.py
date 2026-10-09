# SPDX-License-Identifier: MIT
"""Offline, non-executing preflight for the October 2026 poisoned ESLint config.

This is an indicator check, NOT a malware scanner or permission to run a repo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

SCHEMA = "tjl-repo-exec-quarantine/v1"
HOSTILE_GIT_BLOBS = frozenset({"7da565bcb57517fa1c3adc1c824b7e105dae2699"})
CANDIDATES = ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs")
MAX_FILE_BYTES = 2_000_000

# Combined behavior, not superficial minification, triggers a *manual review*.
_EVAL = re.compile(r"\beval\s*\(")
_SPAWN = re.compile(r"\b(?:spawn|exec|execSync|spawnSync)\s*\(")
_OBFUSCATED = re.compile(r"(?:[A-Za-z0-9_$]{90,}|[A-Za-z0-9+/=]{180,})")


def git_blob_sha(data: bytes) -> str:
    """Compute the *Git blob* SHA-1, not the ordinary file SHA-1."""
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def scan_repository(root: Path, *, hostile_blobs: Iterable[str] = HOSTILE_GIT_BLOBS) -> dict[str, Any]:
    """Inspect root ESLint configs as bytes. Never import, parse or run JavaScript."""
    bad = frozenset(hostile_blobs)
    results: list[dict[str, Any]] = []
    if root.is_symlink() or not root.is_dir():
        return {"schema": SCHEMA, "disposition": "REVIEW_REQUIRED", "checks": [],
                "reason": "repository root is absent, not a directory, or is a symlink"}

    for name in CANDIDATES:
        path = root / name
        if path.is_symlink():
            results.append({"path": name, "status": "REVIEW_REQUIRED", "reason": "symlink not followed"})
            continue
        try:
            if not path.exists():
                continue
            if not path.is_file():
                results.append({"path": name, "status": "REVIEW_REQUIRED", "reason": "not a regular file"})
                continue
            with path.open("rb") as f:
                raw = f.read(MAX_FILE_BYTES + 1)
        except OSError:
            results.append({"path": name, "status": "REVIEW_REQUIRED", "reason": "config cannot be read"})
            continue
        if len(raw) > MAX_FILE_BYTES:
            results.append({"path": name, "status": "REVIEW_REQUIRED", "reason": "config exceeds byte limit"})
            continue
        blob_sha = git_blob_sha(raw)
        check: dict[str, Any] = {"path": name, "git_blob_sha": blob_sha, "bytes": len(raw)}
        if blob_sha in bad:
            check["status"] = "KNOWN_HOSTILE_BLOB"
        else:
            try:
                source = raw.decode("utf-8")
            except UnicodeDecodeError:
                check["status"] = "REVIEW_REQUIRED"
                check["reason"] = "non-UTF8 executable configuration"
            else:
                if _EVAL.search(source) and _SPAWN.search(source) and _OBFUSCATED.search(source):
                    check["status"] = "SUSPICIOUS_EXECUTION_REVIEW"
                else:
                    check["status"] = "NO_KNOWN_INDICATOR"
        results.append(check)

    statuses = {x["status"] for x in results}
    disposition = ("QUARANTINE" if "KNOWN_HOSTILE_BLOB" in statuses
                   else "REVIEW_REQUIRED" if statuses & {"REVIEW_REQUIRED", "SUSPICIOUS_EXECUTION_REVIEW"}
                   else "NO_KNOWN_INDICATOR")
    return {
        "schema": SCHEMA, "disposition": disposition, "checks": results,
        "scope": "Only root ESLint config files are checked. No clean/safe-to-execute assertion.",
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
