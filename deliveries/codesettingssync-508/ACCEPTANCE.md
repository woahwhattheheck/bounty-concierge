# code-settings-sync #508 — pre-download change preview

Canonical issue: https://github.com/shanalikhan/code-settings-sync/issues/508
Funding observed on the issue: IssueHunt $20 funded.
Issue state at intake: OPEN, unassigned.

## Residual scope

Existing external PR #1474 is open and mergeable and adds upload-side planning/preview. Current upstream download flow still builds `updatedFiles` and then immediately uninstalls/installs extensions and writes files before `ShowSummaryOutput` runs. This packet covers only that remaining download-side behavior.

## Candidate

Apply the packet paths as follows:

- `src/service/download-change.service.ts` -> upstream `src/service/download-change.service.ts`
- `test/service/download-change.service.test.ts` -> upstream `test/service/download-change.service.test.ts`
- apply `SYNC.diff` to upstream `src/sync.ts`

The download plan contains the exact processed file bytes, target paths, create/update classification, and extension install/remove objects that will later be applied.

Behavior:
- unchanged effective local files are excluded;
- pragma/preserve processing happens before comparison, so the preview reflects the bytes that would actually be written;
- platform/universal keybinding selection and custom-file filtering remain intact;
- extension install/remove sets are computed before any extension mutation;
- non-quiet sync prompts from the exact computed plan;
- cancel returns before file writes or extension install/remove calls and restarts auto-upload watching when it had been enabled;
- quiet sync remains non-interactive;
- after confirmation, file writes and extension operations consume the same plan;
- the final summary receives only the file changes that were actually planned/applied;
- existing `forceDownload` freshness behavior is unchanged.

## Focused evidence

1. `SYNC.diff` was parsed and applied in-memory against the exact current upstream `src/sync.ts` read. All 3 hunks matched their source context.
2. Structural readback of the resulting candidate confirmed:
   - the new plan import is present;
   - `ConfirmPlan` occurs before `FileService.WriteFile(change.targetPath, change.content)`;
   - the old pre-confirm `PluginService.DeleteExtensions` mutator is removed from the download loop;
   - cancel restarts `HandleStartWatching` when `autoUpload` is enabled;
   - `ShowSummaryOutput` consumes `changePlan.fileChanges`.
3. The helper was compiled under TypeScript 5.8.3 with strict checking against minimal interface-compatible stubs and executed on Node 22.16.0. Observed behavior:
   - unchanged file -> excluded;
   - actions -> updated, created;
   - preview -> new/changed file plus install/remove extension sets;
   - canceled confirmation -> false;
   - quiet sync -> true with no prompt;
   - nested `snippets|x.json` target -> platform-separated path.

The repository dependency tree, full extension host, Chai suite, and hosted CI were not executed in this cloud harness. Run the project-maintained focused test plus normal CI after publishing.

Suggested focused command:
`npm test -- --grep "DownloadChangeService"`
(adapt to the repository's actual test script if its CLI does not forward grep.)

## Publication notes

This is a complete source packet, not an upstream PR or payout receipt. The shared account currently has no writable `woahwhattheheck/code-settings-sync` fork and this connector exposes no fork-create primitive. Refresh upstream and assignment state before publication.

If PR #1474 lands first, retain its upload-side `GistChangeService` work and reconcile only ordinary `sync.ts` import/context drift; this packet's download service and download-loop semantics are disjoint.

Suggested PR title:
`feat(sync): preview download changes before applying them`

Suggested body starts:
`Fixes #508.`

Summarize that download now computes and displays the exact effective file/extension plan before mutation, cancel performs no file or extension changes, quiet sync remains non-interactive, and final summary/application consume the same plan.
