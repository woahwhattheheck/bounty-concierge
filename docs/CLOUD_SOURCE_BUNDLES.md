# Pinned source bundles for cloud workers

Use this when the GitHub connector works but the worker container cannot clone
or reach raw GitHub files. One source archive can serve every worker using the
same commit; do not reconstruct the checkout through hundreds of file reads.

This is source transport, not a build result, bounty claim, payment check or
current-state cache. Refresh the relevant live claim and GitHub head before
editing or publishing. The export never executes code from the requested commit.

## Reuse before requesting

Resolve the exact 40-character lowercase commit SHA needed for the task. Search
repository artifact metadata for the name `source-<SHA>` using the native GitHub
`fetch` action on this endpoint:

```text
https://api.github.com/repos/woahwhattheheck/bounty-concierge/actions/artifacts?name=source-<SHA>&per_page=20
```

Reuse an unexpired artifact from the source-bundle workflow with that identity.
Share its run ID, artifact ID and commit SHA in the existing work thread. An
artifact has seven-day retention; it is not permanent storage. Do not dispatch
one export per worker, or export every advancing main commit without a task.

## Request using native connector writes

The workflow accepts a manual `workflow_dispatch` in the GitHub UI, or this
connector-only path when a workflow-dispatch action is unavailable:

1. Create branch `source-bundle/request-<SHA>` from `main` with
   `GitHub.create_branch`. The base must contain
   `.github/workflows/source_bundle.yml`.
2. On that branch, create `.github/source-bundle-request.json` with
   `GitHub.create_file`, containing `{"requested_sha":"<SHA>"}`.
   The filename change triggers the workflow. The SHA in the validated branch
   name is the source identity; the JSON is only an explicit request record.
3. Record the resulting request commit SHA. Read its push run through
   `GitHub.fetch` on
   `/repos/woahwhattheheck/bounty-concierge/actions/runs?head_sha=<REQUEST_COMMIT>&event=push&per_page=5`
   under `https://api.github.com`.

If that request branch already exists, inspect/reuse its run before writing
anything. Do not overwrite a peer's request or create alternate request branch
names to defeat reuse. When an expired artifact needs replacing, coordinate a
single rerun of its existing workflow job. Do useful local work between bounded
status reads; do not turn the run check into a polling loop.

The push trigger applies only to the request branches and the request-record
path. Ordinary pushes, pull requests and scheduled activity do not export
archives. The workflow has read-only repository permissions, a five-minute
ceiling, shallow checkout and no retained Git credentials. Same-request
concurrency does not cancel a running export.

## Download through the connector

After a successful run, call:

```text
GitHub.fetch_workflow_run_artifacts(
    repo_full_name="woahwhattheheck/bounty-concierge",
    run_id=<RUN_ID>, name="source-<SHA>")

GitHub.download_workflow_artifact(
    repo_full_name="woahwhattheheck/bounty-concierge",
    artifact_id=<ARTIFACT_ID>, file_name="source-<SHA>.zip")
```

Use the actual returned file reference or mounted path. A title or download URL
is not a container path. Materialize the returned connector file through the
Files capability only when it is not already mounted; do not invent a path.

The outer ZIP contains `manifest.json` and `source.tar.gz`. Verify the manifest
repository and commit against the requested identity, plus the tarball SHA-256
and byte length, before extracting into a fresh working directory. The following
Python example works with a tarfile implementation that supports the `data`
extraction filter; it performs no network requests and executes no source code:

```python
import hashlib
import io
import json
from pathlib import Path
import tarfile
import zipfile

zip_path = Path("REPLACE_WITH_RETURNED_LOCAL_ZIP_PATH")
expected_sha = "REPLACE_WITH_REQUESTED_40_CHARACTER_SHA"
destination = Path("fresh-source-workdir")
with zipfile.ZipFile(zip_path) as bundle:
    manifest = json.loads(bundle.read("manifest.json"))
    if (manifest.get("schema") != "github-source-bundle/v1"
            or manifest.get("repository") != "woahwhattheheck/bounty-concierge"
            or manifest.get("commit_sha") != expected_sha):
        raise ValueError("source identity mismatch")
    archive = bundle.read("source.tar.gz")
    if (len(archive) != manifest["archive"]["size_bytes"]
            or hashlib.sha256(archive).hexdigest() != manifest["archive"]["sha256"]):
        raise ValueError("source archive integrity mismatch")
    destination.mkdir()  # Refuse to overwrite an existing working directory.
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
        source.extractall(destination, filter="data")
print(destination / "source")
```

The inner tar preserves executable modes, symlinks and tracked dotfiles that an
ordinary outer artifact ZIP may not. This is a `git archive` export: repository
export attributes apply, Git history is absent, submodules are not recursively
included, and Git LFS files remain pointers. Install only the dependencies needed
for the chosen task. Publish edits through the normal branch/PR workflow against
a freshly checked base; the exported directory is not itself a Git checkout.
