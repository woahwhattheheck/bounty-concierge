#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Unpack an existing source-bundle artifact without network or source execution.

Requires Python 3.12+ (tarfile's data extraction filter). Run this file directly;
no concierge package, credentials, Git installation, or dependencies are needed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import tempfile
from typing import Any
import zipfile

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+\Z")
_CHUNK_BYTES = 1024 * 1024
_MANIFEST_BYTES = 64 * 1024


def _repository(value: Any) -> str:
    if (not isinstance(value, str) or not _REPOSITORY.fullmatch(value)
            or value.split("/")[1] in {".", ".."}):
        raise ValueError("repository must be an owner/repository name")
    return value.casefold()


def _path(value: str) -> str:
    """Accept repository-relative paths, never filesystem or parent paths."""
    if (not isinstance(value, str) or not value or "\\" in value or "\x00" in value
            or value.startswith("/") or ".." in value.split("/")):
        raise ValueError("paths must be repository-relative without parent components")
    result = PurePosixPath(value).as_posix()
    if result == ".":
        raise ValueError("omit --path to extract the whole source")
    return result


def _manifest(bundle: zipfile.ZipFile, repository: str, commit: str) -> dict[str, Any]:
    names = bundle.namelist()
    if names.count("manifest.json") != 1 or names.count("source.tar.gz") != 1:
        raise ValueError("artifact must contain one manifest.json and one source.tar.gz")
    if bundle.getinfo("manifest.json").file_size > _MANIFEST_BYTES:
        raise ValueError("source manifest exceeds 64 KiB")
    with bundle.open("manifest.json") as source:
        manifest = json.loads(source.read(_MANIFEST_BYTES + 1))
    if not isinstance(manifest, dict):
        raise ValueError("source manifest must be a JSON object")
    if (manifest.get("schema") != "github-source-bundle/v1"
            or _repository(manifest.get("repository")) != repository
            or manifest.get("commit_sha") != commit):
        raise ValueError("source identity mismatch")
    tree = manifest.get("tree_sha")
    archive = manifest.get("archive")
    if not isinstance(tree, str) or not _SHA.fullmatch(tree):
        raise ValueError("source manifest has no valid tree identity")
    if (not isinstance(archive, dict) or archive.get("file") != "source.tar.gz"
            or archive.get("format") != "git-archive/tar.gz"
            or type(archive.get("size_bytes")) is not int or archive["size_bytes"] < 0
            or not isinstance(archive.get("sha256"), str)
            or not _DIGEST.fullmatch(archive["sha256"])):
        raise ValueError("source manifest archive metadata is invalid")
    if bundle.getinfo("source.tar.gz").file_size != archive["size_bytes"]:
        raise ValueError("source archive length mismatch")
    return manifest


def unpack_bundle(
    zip_path: Path,
    destination: Path,
    *,
    repository: str,
    commit: str,
    paths: list[str] | None = None,
    temp_directory: Path | None = None,
) -> dict[str, Any]:
    """Verify, then extract all or selected paths into an exclusive fresh directory.

    Selection reduces filesystem writes, not download size or archive scanning.
    A selection is a path or directory prefix; dependencies and symlink targets
    are not automatically added. On a handled extraction error, remove only the
    directory created by this invocation. Existing destinations are never used.
    temp_directory optionally selects the existing volume for the verified
    compressed-archive spool; None keeps Python's normal temporary directory.
    """
    if not hasattr(tarfile, "data_filter"):
        raise ValueError("Python with tarfile.data_filter is required (use Python 3.12+)")
    repository = _repository(repository)
    if not isinstance(commit, str) or not _SHA.fullmatch(commit):
        raise ValueError("commit must be a full lowercase 40-character SHA")
    selectors = list(dict.fromkeys(_path(value) for value in (paths or [])))
    if temp_directory is not None:
        temp_directory = Path(temp_directory).expanduser()
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"destination already exists: {destination}")
    matched = {selector: False for selector in selectors}
    counts = {"available_regular_files": 0, "available_file_bytes": 0,
              "extracted_regular_files": 0, "extracted_file_bytes": 0,
              "extracted_symlinks": 0}

    # Spool compressed bytes to disk rather than retaining the whole archive in
    # memory. Verify the complete input before creating the output directory.
    with zipfile.ZipFile(zip_path) as bundle, tempfile.TemporaryFile(dir=temp_directory) as archive:
        manifest = _manifest(bundle, repository, commit)
        digest = hashlib.sha256()
        size = 0
        with bundle.open("source.tar.gz") as source:
            for chunk in iter(lambda: source.read(_CHUNK_BYTES), b""):
                size += len(chunk)
                digest.update(chunk)
                archive.write(chunk)
        if (size != manifest["archive"]["size_bytes"]
                or digest.hexdigest() != manifest["archive"]["sha256"]):
            raise ValueError("source archive integrity mismatch")
        archive.seek(0)
        destination.mkdir()  # Exclusive creation also rejects a concurrent writer.
        try:
            seen: set[str] = set()
            with tarfile.open(fileobj=archive, mode="r|gz") as source:
                for member in source:
                    # Python 3.12 retains TarInfo objects even in stream mode.
                    # Extraction below uses this member directly; seen retains
                    # duplicate detection without keeping the archive index.
                    source.members.clear()
                    name = _path(member.name)
                    if name in seen:
                        raise ValueError(f"duplicate archive path: {name}")
                    seen.add(name)
                    if name == "source":
                        if not member.isdir():
                            raise ValueError("archive source root is not a directory")
                        continue
                    if not name.startswith("source/"):
                        raise ValueError("archive member is outside the source root")
                    if not (member.isfile() or member.isdir() or member.issym()):
                        raise ValueError("archive contains a non-git member type")
                    relative = name[len("source/"):]
                    if member.isfile():
                        counts["available_regular_files"] += 1
                        counts["available_file_bytes"] += member.size
                    selected = not selectors
                    if (
                        len(selectors) > 1
                        and len(selectors) > relative.count("/") + 1
                    ):
                        # Walk ancestors only when fewer than the requested paths.
                        # Mark all matches so overlapping selections count.
                        ancestor = relative
                        while ancestor:
                            if ancestor in matched:
                                matched[ancestor] = True
                                selected = True
                            ancestor = ancestor.rpartition("/")[0]
                    else:
                        for selector in selectors:
                            if relative == selector or relative.startswith(selector + "/"):
                                matched[selector] = True
                                selected = True
                    if selected:
                        source.extract(member, destination, filter="data")
                        if member.isfile():
                            counts["extracted_regular_files"] += 1
                            counts["extracted_file_bytes"] += member.size
                        elif member.issym():
                            counts["extracted_symlinks"] += 1
            missing = [selector for selector, found in matched.items() if not found]
            if missing:
                raise ValueError("selected paths are absent: " + ", ".join(missing))
            (destination / "source").mkdir(exist_ok=True)
        except BaseException:
            shutil.rmtree(destination)
            raise

    return {
        "schema": "source-bundle-unpack/v1",
        "repository": repository,
        "commit_sha": commit,
        "tree_sha": manifest["tree_sha"],
        "archive_sha256": manifest["archive"]["sha256"],
        "archive_bytes": size,
        "source_root": str(destination / "source"),
        "selected_paths": selectors,
        **counts,
        "scope": "Local source extraction only; no source execution or live-state verification.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="actual mounted source-bundle ZIP path")
    parser.add_argument("--repository", required=True, help="expected source owner/repository")
    parser.add_argument("--commit", required=True, help="expected immutable source commit SHA")
    parser.add_argument("--destination", type=Path, required=True, help="new directory; parent must exist")
    parser.add_argument("--path", action="append", default=[], help="file/directory relative to repo; repeatable")
    parser.add_argument("--temp-directory", type=Path,
                        help="existing directory for the compressed archive spool (default: Python temp directory)")
    args = parser.parse_args(argv)
    try:
        result = unpack_bundle(args.artifact, args.destination, repository=args.repository,
                               commit=args.commit, paths=args.path,
                               temp_directory=args.temp_directory)
    except (OSError, ValueError, EOFError, tarfile.TarError, zipfile.BadZipFile,
            RuntimeError, NotImplementedError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
