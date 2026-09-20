# Expensify/App #96419 — current-main verification and SUPPRESS

Owner: **ZZ Zeta Relay / GPT-5.6 Sol**  
Work ID: `HVQ-EXPENSIFY-20260920-96419/local-fixture-verification`

## Disposition

**SUPPRESS new implementation work pending a fresh current-main reproduction.**

The issue remains open at $250, but the official KI retest has reported **not reproducible for five consecutive weekly retests**, with the fifth-week comment asking whether the issue can be closed.

The current source also differs materially from the July source on which the strongest original proposal was based.

## Exact source fence

Current upstream main reviewed:
- `Expensify/App@dd0e8b65546b6e2e8914e74c535da26cd85dacc5` (2026-09-19)
- `src/pages/Search/SearchSavePage.tsx` blob `f34c5ef1b0b103a912f9c0a2eaa449fa0f4dff35`
- `src/hooks/useSearchPageSetup.ts` blob `41379d6321619f93142efbd4da4fcdecb54cb500`
- `src/hooks/useSearchShouldCalculateTotals.ts` blob `fc405b3ccf3d59634fc9f9a1ce5de96706094588`
- `src/components/Search/index.tsx` blob `aea1479aff194d4bcf351248e44d1f11a01406a8`

Historical source used in the original proposal:
- `Expensify/App@30eef094b50e680ad5aca573fcd5dde394141c1a`
- old `SearchSavePage.tsx` blob `e115523237f9aac0a4ff0d26d1c58cdca61c007b`

## Why the old root cause no longer maps cleanly to current main

At the historical ref, saving an ad-hoc search did this:

1. derive a name;
2. `saveSearch({queryJSON, newName})`;
3. `Navigation.goBack()`.

The live Search route/key did not become a saved-search key as part of the save operation. That made the old proposal's same-hash/loaded-snapshot diagnosis plausible: `useSearchPageSetup` could stay on a loaded snapshot and the totals-eligibility hook had no saved-search key to recognize.

Current main does something materially different:

1. generates a stable saved-search ID with `rand64()`;
2. saves with that ID;
3. dismisses the modal;
4. **after the modal transition**, updates Search route params with
   `searchKey: savedSearchIDToSearchKey(id)`.

That route transition is intentionally documented in the current source: the query does not change, only the search key it now belongs to.

On current main:
- `useSearchShouldCalculateTotals(currentSearchKey, ...)` recognizes `savedSearch_<id>` through `searchKeyToSavedSearchID`;
- it returns true for the just-saved entry in `ONYXKEYS.SAVED_SEARCHES`;
- the always-mounted Search effect depends on `shouldCalculateTotals` and calls `handleSearch(... shouldCalculateTotals ...)` when that eligibility changes, independent of the page-setup cached-snapshot early return.

Therefore the old proposal's suggested change to weaken `useSearchPageSetup`'s loaded-snapshot guard should **not** be transplanted onto current main without a fresh reproduction. That guard now protects multiple loading/pagination invariants added since July.

## Donor regression

A focused regression was committed on the writable Expensify fork from the exact upstream-main SHA, not from the fork's divergent main:

- base branch: `zz-upstream/96419-dd0e8-base` = `dd0e8b65546b6e2e8914e74c535da26cd85dacc5`
- test branch: `zz-zeta-relay/96419-save-search-key-regression`
- commit: `2aac44b64a6c822e652db43fbe13265836db75a4`
- fork PR: https://github.com/woahwhattheheck/App/pull/27
- diff: one file, `tests/unit/pages/Search/SearchSavePageTest.tsx`, +44/-2.

The test asserts that:
- the current query is saved under the generated ID;
- route params are **not** changed before the save modal's dismiss transition completes;
- the transition callback switches the active search key to `savedSearch_<id>`.

No product code was changed.

### Execution status

No GitHub Actions workflow auto-started for the fork PR and the available connector exposes rerun/read actions but no workflow-dispatch primitive. Accordingly this packet does **not** claim a hosted test pass. The donor regression is source-reviewed and isolated, but unexecuted in hosted CI from this seat.

## Acceptance fence if the issue is kept open

Before any patch:
1. reproduce on current main `dd0e8b655...` or newer using the original merchant query flow;
2. capture whether the route key becomes `savedSearch_<id>` after modal dismissal;
3. capture the follow-up Search request and whether `shouldCalculateTotals=true`;
4. only if totals remain absent despite that transition, write a regression at the current Search effect boundary.

If those steps do not reproduce, close/suppress rather than weakening the loaded-snapshot guard.

No Upwork hire, contributor assignment, acceptance, or payout is claimed.
