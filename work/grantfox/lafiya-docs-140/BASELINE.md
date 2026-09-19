# GrantFox baseline — Lafiya-xyz/Lafiya-docs #140

Operation: `GFOX2-20260919-026-R-ZZ-SOL-DRIFT`  
Worker: ZZ-Sol-Drift · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `27229a1b898ffb14a8338676ca2a5080f9786dff`

## Canonical issue

- GitHub: https://github.com/Lafiya-xyz/Lafiya-docs/issues/140
- GrantFox: https://contribute.grantfox.xyz/org/Lafiya-xyz/repo/Lafiya-docs/issue/140
- State at observation: OPEN
- GitHub assignee: none
- GrantFox public assignment state: Unassigned
- Labels observed: `stellar`, `wave`, `advanced`, `Maybe Rewarded`, `GrantFox OSS`, `Third Campaign`
- Existing issue comments before this packet: 1 application comment from another user
- Matching PR census for #140: no carrier surfaced in the fresh connector search
- Reward interpretation: campaign / `Maybe Rewarded` labels are eligibility signals only. No fixed award or payment is asserted.

This packet is intentionally pre-assignment evidence. It does not implement the upstream dashboard.

## Current-source finding

The requested dashboard depends on three signal producers, but only one is implemented on the pinned upstream head.

### 1. Markdown lint — implemented

`.github/workflows/markdown-lint.yml` is the only workflow present in the current repository tree. It runs on pull requests and pushes to `main`, checks out the repository, and invokes `DavidAnson/markdownlint-cli2-action@v16` over `**/*.md`.

Pinned blob:

- `.github/workflows/markdown-lint.yml`: `022dfd31ce3cbc68c8d9179aa397ca94ec089e30`

### 2. Shared-contract synchronization — requested but not yet implemented

Open issue #134 separately requests a README ↔ `docs/data-model.md` drift detector. #134 is still open and unassigned at observation time, and no corresponding workflow/script exists in the pinned #140 tree.

Its sole current application comment proposes Soroban `require_auth` / gas-optimization work that does not match the documentation-sync issue, so it is not evidence that the producer exists.

### 3. Broken-link signal — no producer found on current main

No link-check workflow or script appears in the pinned repository tree. The PR template only contains a manual checklist item asking contributors to ensure links resolve.

Pinned supporting blobs:

- `.github/PULL_REQUEST_TEMPLATE.md`: `ad6308b0ceb840c98116ebc6fffcfaafb8c111fa`
- `CONTRIBUTING.md`: `1a6ca990ebca0cbb79cee4ac15ad9ce1e4dbafbb`

## Dashboard contract implied by the live source

A correct #140 implementation should not silently convert an absent producer into a green check.

Use a small aggregation contract with one record per required signal:

- `name`: `lint` | `links` | `sync`
- `status`: `pass` | `fail` | `unavailable`
- `source`: producer job/artifact identity
- `observed_at`: build timestamp
- `details`: bounded human-readable explanation
- `evidence`: artifact/path or producer receipt when available

The aggregator should emit:

1. stable machine-readable JSON for downstream automation;
2. a human Markdown summary suitable for a GitHub Actions artifact or PR comment;
3. the current build identity and upstream commit;
4. explicit `unavailable` state for missing/not-yet-landed producers;
5. snapshot input suitable for append-only trend analysis without making history mutable from a PR job.

Producer jobs should remain separable so #134 and a future link-check implementation can land independently without rewriting the dashboard schema.

## Narrow implementation plan after assignment

1. Define and document the signal schema above.
2. Preserve the existing markdown-lint job and translate its outcome into the schema.
3. Add/consume a broken-link producer rather than faking a result.
4. Consume #134's sync producer when it exists; until then report `unavailable`.
5. Aggregate into JSON + Markdown in a dedicated docs-health job.
6. Upload the summary and JSON as per-build Actions artifacts.
7. Add fixtures/tests for all-pass, one-fail, and missing-producer cases.
8. Treat trend tracking as historical snapshot consumption; do not let an untrusted PR mutate canonical history.

## Verification plan

Pre-assignment checks completed through GitHub connector reads:

- current tree at `27229a1b898ffb14a8338676ca2a5080f9786dff`
- workflow inventory
- #134/#140 issue and comment state
- exact workflow/template/contributing blobs
- open/matching PR census

After assignment, run the repository's existing local markdown command plus focused aggregator tests and a workflow syntax/action dry run where available. Record exact commands and exit codes.

## Application draft

> Applying for Lafiya-docs #140 after checking current `main` at `27229a1b898ffb14a8338676ca2a5080f9786dff`.
>
> I first mapped the three inputs the dashboard is supposed to aggregate. Markdown lint exists today as the repository's only workflow. The shared-contract drift signal is still separately tracked by open #134 and has not landed on main, and I found no broken-link CI producer on the pinned tree. I therefore would not represent missing producers as passing.
>
> My approach after assignment is to define one stable per-signal contract (`pass` / `fail` / `unavailable` + evidence), preserve the existing lint behavior, add/consume real link and sync producers, and emit both machine JSON and a human Markdown report as per-build Actions artifacts. I would also retain build snapshots in a form that supports trend analysis without allowing a PR job to mutate canonical history. Tests will cover all-pass, failure, and unavailable-producer cases.
>
> I will keep this as one coherent CI/reporting change and wait for provider/maintainer assignment before upstream implementation.

## Authority boundary

The authenticated GitHub connector can read the upstream repository but its repository permission snapshot is `pull=true`, `push=false`. The owned `woahwhattheheck/bounty-concierge` repository is used only to preserve this source census and application packet.

No upstream source, issue state, provider assignment, wallet, funds, award, or payment is mutated by this artifact.
