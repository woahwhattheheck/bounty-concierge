# Pinned source bundles for cloud workers

Use this when the GitHub connector works but the worker container cannot clone
or reach raw GitHub files. One source archive can serve every worker using the
same commit; do not reconstruct the checkout through hundreds of file reads.

This is source transport, not a build result, bounty claim, payment check or
current-state cache. Refresh the relevant live claim and GitHub head before
editing or publishing. The export never executes code from the requested commit.

The same exporter supports its own repository and explicitly public repositories
on GitHub. Cross-repository requests verify public visibility before checkout;
private/internal sources, missing visibility, renamed identities and access
failures stop the export. No extra credential, permission bypass or owner-PC
copy is supported. Normal same-repository use is unchanged.

## Reuse before requesting

Resolve the exact 40-character lowercase commit SHA needed for the task. First
reuse a run/artifact receipt from the shared work thread. For its own source,
the artifact name remains `source-<SHA>`. For another repository, lowercase its
owner/name and use `source-<owner>__<repository>-<SHA>`. Different forks can
contain the same commit, so do not infer the source repository from SHA alone.

Without a shared receipt, read the canonical request branch described below
through the native GitHub `fetch` action:

```text
https://api.github.com/repos/woahwhattheheck/bounty-concierge/git/ref/heads/<CANONICAL_REQUEST_BRANCH>
```

A successful ref read gives the request commit. Read its push runs at
`/repos/woahwhattheheck/bounty-concierge/actions/runs?head_sha=<REQUEST_COMMIT>&event=push&per_page=5`,
then use the supported `fetch_workflow_run_artifacts` action for the matching
successful source-export run and exact artifact name. A confirmed missing
request branch allows one coordinated request; access errors do not establish
absence. For a manual dispatch, reuse its shared run ID.

Repository-wide `/actions/artifacts?name=...` lookup was rejected by this
connector's generic `fetch` action. Do not depend on that unsupported shortcut
or substitute an unverified file title for an artifact ID. Run-level artifact
reads and native downloads are the supported transport path.

Reuse an unexpired artifact from the source-bundle workflow with that identity.
Share its run ID, artifact ID and commit SHA in the existing work thread. An
artifact has seven-day retention; it is not permanent storage. Do not dispatch
one export per worker, or export every advancing main commit without a task.

## Request using native connector writes

The workflow accepts a manual `workflow_dispatch` in the GitHub UI with
`source_sha` and optional `source_repository` (blank means the exporter repo),
or this connector-only path when a workflow-dispatch action is unavailable:

1. Create branch `source-bundle/request-<SHA>` from `main` with
   `GitHub.create_branch`. The base must contain
   `.github/workflows/source_bundle.yml`. For another public repository use
   `source-bundle/repository-<owner>/<repository>/<SHA>` instead, with lowercase
   owner/repository. Both request branches live in the exporter repository.
2. On that branch, create `.github/source-bundle-request.json` with
   `GitHub.create_file`, containing `{"requested_sha":"<SHA>"}`.
   For another repository include `"source_repository":"<owner>/<repository>"`
   alongside `requested_sha`. The filename change triggers the workflow. The
   validated branch name supplies the source identity; the JSON is only an
   explicit request record, not an unchecked checkout instruction.
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
concurrency does not cancel a running export. Cross-repository requests add one
read of the target's GitHub metadata, not a catalog traversal or background poll.

### Cross-repository example

For `woahwhattheheck/RemitFlow-Backend` commit
`6c013e9d5873d72416bbb2ff66459d97baf689fb`, the canonical request branch is:

```text
source-bundle/repository-woahwhattheheck/remitflow-backend/6c013e9d5873d72416bbb2ff66459d97baf689fb
```

The artifact name is:

```text
source-woahwhattheheck__remitflow-backend-6c013e9d5873d72416bbb2ff66459d97baf689fb
```

Use an existing successful request/artifact when present. The JSON request is
`{"source_repository":"woahwhattheheck/remitflow-backend","requested_sha":"6c013e9d5873d72416bbb2ff66459d97baf689fb"}`.
No workflow needs to be installed in the source repository.

## Download through the connector

After a successful run, call:

```text
GitHub.fetch_workflow_run_artifacts(
    repo_full_name="woahwhattheheck/bounty-concierge",
    run_id=<RUN_ID>, name="<EXACT_ARTIFACT_NAME>")

GitHub.download_workflow_artifact(
    repo_full_name="woahwhattheheck/bounty-concierge",
    artifact_id=<ARTIFACT_ID>, file_name="<EXACT_ARTIFACT_NAME>.zip")
```

Use the actual returned file reference or mounted path. A title or download URL
is not a container path. Materialize the returned connector file through the
Files capability only when it is not already mounted; do not invent a path.

In a code-mode cloud harness that exposes `download_file`, materialize the
returned connector `file_id` through that tool. For example:

```javascript
const local = await tools.download_file({file_id: "<RETURNED_FILE_ID>"});
text(local); // Use the returned local path for the extraction example below.
```

Use the actual file ID from the artifact response (for example,
`file_uri.file_id`), not the artifact ID or filename. A direct HTTP 403 from the
temporary download URL does not establish a GitHub export or repository-access
failure: the authorized file materializer can still work. Reuse the same
artifact and returned file reference; do not dispatch a new export for this
transport state or reuse another worker's local filesystem path.

For a reusable command instead of copying the extraction recipe, use
[`tools/unpack_source_bundle.py`](../tools/unpack_source_bundle.py) with Python
3.12+. It runs standalone without a checkout or package installation; a worker
can fetch that one script through the native connector. Supply the actual
mounted ZIP, expected source repository/commit and a fresh destination. Repeat
`--path` to materialize selected files or directories, or omit it for all source.
See [the command guide](SOURCE_BUNDLE_UNPACK.md) for invocation and the executed
artifact receipt. Selection reduces filesystem writes, not download bytes; the
complete archive is still verified and scanned. This does not replace live
head/claim checks or change how source exports are requested.

The outer ZIP contains `manifest.json` and `source.tar.gz`. Verify the manifest
source `repository` and commit against the requested identity, plus the tarball SHA-256
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
expected_repository = "REPLACE_WITH_LOWERCASE_OWNER/REPOSITORY"
destination = Path("fresh-source-workdir")
with zipfile.ZipFile(zip_path) as bundle:
    manifest = json.loads(bundle.read("manifest.json"))
    if (manifest.get("schema") != "github-source-bundle/v1"
            or manifest.get("repository", "").lower() != expected_repository
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

New manifests distinguish the source `repository` from `exporter_repository`,
which owns the run and artifact. Existing v1 same-repository manifests without
`exporter_repository` remain usable; never substitute exporter identity for the
expected source identity.

The inner tar preserves executable modes, symlinks and tracked dotfiles that an
ordinary outer artifact ZIP may not. This is a `git archive` export: repository
export attributes apply, Git history is absent, submodules are not recursively
included, and Git LFS files remain pointers. Install only the dependencies needed
for the chosen task. Publish edits through the normal branch/PR workflow against
a freshly checked base; the exported directory is not itself a Git checkout.


## Executed cross-repository receipt — October 4, 2026

The RemitFlow example above was exported successfully by
[run 37191211160](https://github.com/woahwhattheheck/bounty-concierge/actions/runs/37191211160),
request commit `497cb63e15139e9ca3985e7d3af3ad78df8a2574`. Its workflow bytes are
blob `c786b809ef5dc705b4796d13bf2998f4e01030e6`; later guide updates do not
change that executed workflow.

Reuse artifact **11298493947** through the existing native download action until
its reported expiry, **October 11, 2026 at 09:09:02 UTC**. No repeat export is
needed for this source identity.

- Source: `woahwhattheheck/remitflow-backend` at
  `6c013e9d5873d72416bbb2ff66459d97baf689fb`, tree
  `50f3bb47d9931124156eebad59d85ebbba8e9110`.
- Exporter: `woahwhattheheck/bounty-concierge`; actual run completed successfully
  at 09:09:06 UTC, with no target-repository workflow installed or executed.
- Native artifact download mounted **180 regular source files**, totaling
  **374,702 uncompressed file bytes**, from a **102,078-byte outer ZIP**.
- Outer ZIP SHA-256 matched GitHub artifact metadata:
  `6e04f42abfb1f4685fa92b48de3aa060a639827151252714fb8aad650c751875`.
- Inner tarball was 101,179 bytes and matched the manifest SHA-256:
  `5a4d68b72b5451850065cfe22e13a19c204f83f38eb802d1c9f979ef2b132870`.

The complete source and exporter identities, commit, tree, lengths and digests
were checked before data-filtered extraction. Both changed services, both new
regression files, the benchmark script and its report matched the six previously
published RemitFlow files byte-for-byte. This is actual source transport proof,
not application execution, a dependency cache, bounty eligibility or payment.

Twelve local identity-step cases also passed: legacy request/dispatch,
cross-repository request/dispatch with case normalization, and malformed SHA,
moving ref, URL/path traversal, private/internal/missing visibility and renamed
repository rejection. Those local cases mocked metadata HTTP and are distinct
from the successful real public-repository export above.
