#!/usr/bin/env python3
"""Focused before/after measurement for source-bundle selector matching."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import statistics
import tarfile
import tempfile
import time
import zipfile

REPOSITORY = "measurement/source-bundle"
COMMIT = "a" * 40
TREE = "b" * 40


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fingerprint(root: Path):
    return {
        str(path.relative_to(root)): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mode & 0o777,
        )
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def make_bundle(root: Path, label: str, count: int):
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as tar:
            names = ["source", "source/src"] + [
                f"source/src/group_{group:03d}" for group in range((count + 63) // 64)
            ]
            for name in names:
                item = tarfile.TarInfo(name)
                item.type = tarfile.DIRTYPE
                item.mode = 0o755
                tar.addfile(item)
            for index in range(count):
                item = tarfile.TarInfo(
                    f"source/src/group_{index // 64:03d}/file_{index:05d}.txt"
                )
                content = (f"file {index:05d}\n".encode() * 8)[:64]
                item.size = len(content)
                item.mode = 0o644
                tar.addfile(item, io.BytesIO(content))
    data = compressed.getvalue()
    manifest = {
        "schema": "github-source-bundle/v1",
        "repository": REPOSITORY,
        "commit_sha": COMMIT,
        "tree_sha": TREE,
        "archive": {
            "file": "source.tar.gz", "format": "git-archive/tar.gz",
            "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        },
    }
    target = root / f"{label}.zip"
    with zipfile.ZipFile(target, "w") as bundle:
        for name, content in [
            ("manifest.json", json.dumps(manifest, sort_keys=True).encode()),
            ("source.tar.gz", data),
        ]:
            item = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(item, content)
    return target


def source_identity(path: Path):
    content = path.read_bytes()
    return {
        "sha256": hashlib.sha256(content).hexdigest(),
        "git_blob_sha": hashlib.sha1(
            b"blob " + str(len(content)).encode() + b"\0" + content
        ).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tempfile.tempdir = "/dev/shm"
    modules = {"before": load(args.before, "before_unpack"),
               "after": load(args.after, "after_unpack")}
    result = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": __import__("sys").version,
                        "platform": __import__("platform").platform(),
                        "temporary_filesystem": "/dev/shm (tmpfs; shared overlay was full)"},
        "before_source": source_identity(args.before),
        "after_source": source_identity(args.after),
        "fixture_scope": "Deterministic synthetic v1 artifacts; manifest identities are fixture markers, not GitHub commits.",
        "timing_scope": "unpack_bundle call only, including ZIP reads, complete SHA-256 verification, archive scanning and selected-file extraction; output fingerprint and cleanup excluded.",
        "provider_requests": 0,
        "workloads": {},
    }
    with tempfile.TemporaryDirectory(prefix="unpack-prefix-b3ad-") as temporary:
        root = Path(temporary)
        large = make_bundle(root, "multi", 4096)
        small = make_bundle(root, "single", 32)
        multiple = [
            f"src/group_{index // 64:03d}/file_{index:05d}.txt"
            for index in range(0, 4096, 16)
        ] + ["src/group_000", "src/group_000/file_00000.txt"]
        workloads = {
            "multi_4096_files": (large, multiple),
            "single_32_files": (small, ["src/group_000/file_00007.txt"]),
        }
        for label, (bundle, selectors) in workloads.items():
            reference = None
            times = {"before": [], "after": []}
            receipt = None
            for repetition in range(5):
                order = ["before", "after"] if repetition % 2 == 0 else ["after", "before"]
                for name in order:
                    destination = root / f"{label}-{repetition}-{name}"
                    start = time.perf_counter_ns()
                    actual = modules[name].unpack_bundle(
                        bundle, destination, repository=REPOSITORY,
                        commit=COMMIT, paths=selectors,
                    )
                    times[name].append((time.perf_counter_ns() - start) / 1_000_000)
                    files = fingerprint(destination / "source")
                    comparable = dict(actual)
                    comparable.pop("source_root")
                    observed = (comparable, files)
                    if reference is None:
                        reference = observed
                    elif observed != reference:
                        raise AssertionError(f"Result or extracted bytes/modes differ: {label}/{name}")
                    receipt = comparable
                    shutil.rmtree(destination)
            before_ms = statistics.median(times["before"])
            after_ms = statistics.median(times["after"])
            result["workloads"][label] = {
                "input_selectors": len(selectors),
                "unique_selectors": len(receipt["selected_paths"]),
                "artifact_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                "archive_bytes": receipt["archive_bytes"],
                "available_files": receipt["available_regular_files"],
                "extracted_files": receipt["extracted_regular_files"],
                "extracted_bytes": receipt["extracted_file_bytes"],
                "all_receipts_files_and_modes_equal": True,
                "milliseconds": times,
                "median_before_ms": before_ms,
                "median_after_ms": after_ms,
                "median_reduction_percent": (before_ms - after_ms) / before_ms * 100,
            }
        # Check a misleading prefix and cleanup after a missing selection.
        # These short lists use the retained direct-comparison branch.
        failures = {}
        for label, selectors in {
            "component_boundary": ["src/group_00", "src/group_000/file_00000.txt"],
            "missing_selector": ["src/group_000/file_00000.txt", "missing.txt"],
        }.items():
            errors = {}
            for name, module in modules.items():
                destination = root / f"{label}-{name}"
                try:
                    module.unpack_bundle(small, destination, repository=REPOSITORY,
                                         commit=COMMIT, paths=selectors)
                except ValueError as error:
                    errors[name] = str(error)
                else:
                    raise AssertionError(f"{label} unexpectedly succeeded")
                if destination.exists():
                    raise AssertionError(f"{label} left a partial destination")
            if errors["before"] != errors["after"]:
                raise AssertionError(f"{label} errors differ")
            failures[label] = {"same_error": errors["after"], "destinations_removed": True}
        result["focused_semantics"] = failures
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "workloads": result["workloads"],
        "focused_semantics": result["focused_semantics"],
        "before_source": result["before_source"],
        "after_source": result["after_source"],
    }, indent=2))


if __name__ == "__main__":
    main()

