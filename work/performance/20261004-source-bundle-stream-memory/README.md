# Source-bundle extractor memory measurement

The stream reader released each consumed `TarInfo` from `TarFile.members` instead of retaining the archive index. Extraction receives the current member object directly. The independent normalized-path set continues to reject duplicate entries, and the existing data extraction filter is unchanged.

On Python 3.12.14, the final controlled pair scanned **7,222 real Python source files (116,920,305 bytes)** and extracted the same two files (34,951 bytes). The input was made by copying the installed runtime's regular `*.py` files into a new local Git repository and packaging `git archive --prefix=source/` in the existing source-bundle format. Its local commit is not an upstream project revision. Input, program and extracted-file digests are in [measurement.json](measurement.json).

| Measurement | Baseline | Changed |
| --- | ---: | ---: |
| Peak process RSS, KiB | 21,988 | 15,592 |
| Elapsed seconds | 1.057192 | 0.955030 |
| Regular files scanned | 7,222 | 7,222 |
| Regular files extracted | 2 | 2 |
| Extracted bytes | 34,951 | 34,951 |
| CLI exit status | 0 | 0 |

Peak process RSS fell **6,396 KiB (29.1%)** in this pair. All selected file hashes and result metadata matched, except the destination path.

## Method and limits

Each program ran as a real CLI subprocess from a fresh measurement process. The final pair used the same bundle, selections and tmpfs placement for input, output and compressed-archive spool. This avoided a shared-disk ENOSPC condition; the earlier run history is retained in the record. Tmpfs backing memory is not process RSS.

This is one sequential pair on one local workload. The timing difference is descriptive, not a repeatable speedup or fleet-throughput claim. The extractor still scans the whole archive, verifies its complete digest and retains the set of seen path names; memory is not constant in the number of entries. No network calls or extracted source execution were involved.

## Repeat on an existing source bundle

Use the baseline blob `1ba52712f06fe5303214231fe5e4a589889d56ac` and changed blob `37c315d7237950cd501e96f844ba72782c86372b`. Run this wrapper once per version in a fresh process, with the same artifact and selectors and a different fresh destination. Set `TMPDIR` to the same filesystem for both runs.

```sh
python3 - baseline.py bundle.zip --repository OWNER/REPO --commit FULL_SHA \
  --destination fresh-baseline --path path/to/selected-file <<'PY'
import json, resource, subprocess, sys, time
command = [sys.executable, *sys.argv[1:]]
started = time.perf_counter()
result = subprocess.run(command, capture_output=True, text=True)
elapsed = time.perf_counter() - started
usage = resource.getrusage(resource.RUSAGE_CHILDREN)
print(json.dumps({
    "command": command, "returncode": result.returncode,
    "elapsed_seconds": elapsed, "max_rss_kib": usage.ru_maxrss,
    "stdout": result.stdout, "stderr": result.stderr
}, indent=2))
raise SystemExit(result.returncode)
PY
```

On Linux, `ru_maxrss` is expressed in KiB. Compare successful command output and hashes of every selected file as well as resource use. Python's stream-mode contract is documented in the [Python 3.12 tarfile reference](https://docs.python.org/3.12/library/tarfile.html).
