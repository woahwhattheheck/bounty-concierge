# Unpack an existing source bundle without a checkout

`tools/unpack_source_bundle.py` turns a downloaded source-bundle artifact into
usable local files. It is a standalone standard-library command: Python 3.12+
(or an implementation exposing `tarfile.data_filter`), no package installation,
Git executable, credentials, provider calls, or source execution.

First reuse the exact source artifact through the native download action in
[CLOUD_SOURCE_BUNDLES.md](CLOUD_SOURCE_BUNDLES.md). Use the actual mounted ZIP
path, not a filename inferred from a message. A worker without this utility can
read this one file through the native `GitHub.fetch_file` action and save its
returned UTF-8 content locally; it need not rebuild or export the repository.

```sh
python tools/unpack_source_bundle.py /actual/mounted/source-artifact.zip \
  --repository woahwhattheheck/bounty-concierge \
  --commit 261f6f866b20e4a3b6433f4ba6bf1ac130e72b92 \
  --destination /existing/parent/new-workdir \
  --path concierge/bounty_capture.py \
  --path concierge/bounty_availability.py \
  --path docs/CLOUD_SOURCE_BUNDLES.md
```

Omit every `--path` to extract the complete exported source. Repeat `--path` for
files or directory prefixes, for example `--path concierge --path README.md`.
The destination must not exist and its parent must exist. JSON on standard
output identifies `source_root`, the pinned repository/commit/tree, archive
checksum and available/extracted regular-file counts and byte totals. Errors
produce JSON on standard error and exit status 2.

The command checks the expected **source** repository and commit, including
legacy v1 manifests without `exporter_repository`. It streams the ZIP's tarball
through a temporary file, checks its complete length and SHA-256 before creating
the destination, and uses the tarfile data filter during extraction. It does
not buffer the entire compressed archive in memory. Invalid selections,
unsupported archive member types and extraction failures do not leave a usable
partial destination. Existing directories and edits are never overwritten.

Selection reduces materialized files and filesystem writes, **not download
bytes**: the complete archive is still verified and scanned. Executable files
and safe symlinks use the normal data-filtered archive semantics. A selected
symlink's target or a selected module's dependencies are not automatically
included. The output is not a Git checkout; export attributes, missing history,
non-recursive submodules and LFS pointers have the same limits as the exporter.

The manifest/checksum establish consistency with the supplied artifact, not
independent provider authentication. Obtain the artifact from the existing
trusted workflow receipt. Before publishing edits, refresh the live head and
claim ownership using the normal workflow; this command is neither a new claim
gate nor a current-state cache.

## Executed delivery

On October 4, 2026, the command consumed retained artifact `11298566560` from
run `37190155173`, source commit `261f6f866b20e4a3b6433f4ba6bf1ac130e72b92`.
The full extraction materialized **726 regular files / 6,147,079 bytes**. The
three-path command above materialized **3 files / 59,089 bytes**. All 726 full
files and modes matched a separate data-filtered extraction; the selected files
matched their full-source counterparts byte-for-byte. The command made **zero
provider requests**. This artifact contained no symlinks, so that comparison
alone does not establish a symlink runtime result.

Wrong source identity, altered archive bytes, a missing requested path, a parent
selector and an existing destination were rejected without overwriting the
previous extraction. The two run durations are recorded as observations, not a
repeatable speedup claim. Full results are in
[the source-bound receipt](../work/throughput/source-unpack-20261004/receipt.json).
