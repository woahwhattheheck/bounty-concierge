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

### Select a temporary volume when the default disk is full

`--destination` selects where the requested source files are written. The
compressed archive is verified in a separate temporary file before extraction.
Use `--temp-directory /existing/temporary/directory` to place that file on an
available volume; the directory must already exist. Without the option, Python's
normal temporary-directory selection (including `TMPDIR`) is unchanged. Python
callers can pass `temp_directory=Path(...)` to `unpack_bundle`.

For example, when the container's default disk is full and `/dev/shm` has enough
free space for both the compressed archive and selected source files, add
`--temp-directory /dev/shm` and use a fresh destination under `/dev/shm`. Check
available space first and retain completed edits through the existing GitHub
publication workflow: tmpfs is temporary and consumes shared memory. A failed
temporary-directory selection does not create the output directory or remove
other workers' files. The option does not select a different source artifact or
relax its identity, length, checksum, or extraction checks.

An actual full-disk run of retained artifact `11302078522` failed with the
default temporary-directory selection, then extracted the two requested files
(39,689 bytes) with `--temp-directory` on an available volume. See the
[source-bound receipt](../work/throughput/source-unpack-temp-volume-20261004/receipt.json) for the exact source and archive
identities. This demonstrates recovery from that local storage failure, not a
speedup or a general capacity guarantee.

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

## Reuse a binary source attachment from Slack

When the existing work thread supplies a Slack file ID for a binary source
archive, read that attachment through the exposed native Slack action. In a
code-mode harness exposing these bindings, the returned connector file can be
materialized directly:

```javascript
const attachment = await tools.mcp__codex_apps__slack_slack_read_file({
  file_id: "<SLACK_FILE_ID_FROM_THE_WORK_THREAD>"
});
const fileId = attachment.structuredContent?.file_uri?.file_id;
if (typeof fileId !== "string" || !fileId) {
  throw new Error("Slack did not return a downloadable file reference.");
}
const local = await tools.download_file({file_id: fileId});
text(local); // Use this result's actual local path.
```

Keep the raw Slack response private: it can contain a temporary signed URL.
A failed direct URL download can still leave this authorized file-reference
route usable. Reuse the existing attachment and its returned file ID before
requesting another source export.

Check the packet format before choosing its loader. This unpacker requires
`github-source-bundle/v1`, with `manifest.json` and `source.tar.gz` in the outer
ZIP. A source-postimage ZIP or patch packet uses its own documented loader;
transporting it successfully does not convert it to the source-bundle format.
Retain the packet's source identity and visibility when sharing the recovered
files through the existing work thread.

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
