# Reuse bounty-discovery observations

These optional exclusions record why the October 4 discovery passes did not
start another implementation of selected leads encountered while searching for
Algora work. They are not global defaults, permanent bans, a new approval queue,
or proof that any particular issue is currently listed on Algora or BountyHub.

## Overlapping implementations

| Issue | Directly read matching PR | Observed state and head |
| --- | --- | --- |
| [Days-unit parsing #1](https://github.com/tine1117/oss-hunter-livefire/issues/1) | [PR162](https://github.com/tine1117/oss-hunter-livefire/pull/162) | OPEN, unmerged; `0198218990acdc3382c96960e9e02a14afbeb3bc` |
| [PGlite type generation #2](https://github.com/seveibar/pgstrap/issues/2) | [PR86](https://github.com/seveibar/pgstrap/pull/86) | OPEN, unmerged; `c03496b821b6abcabf39034f74d0848632e8fb86` |
| [Universe PWA branding #3](https://github.com/BAWES-Universe/workadventure-universe/issues/3) | [PR397](https://github.com/BAWES-Universe/workadventure-universe/pull/397) | OPEN, unmerged; `c1d72f9ef3ed810b861ac46fdeb580ea20a5b48d`, targeting `universe` |

The open-PR searches additionally returned days-unit PRs161/160/159/158 and
PGlite PRs85/84. Those search results are not a full enumeration or individual
source reviews. The exact PRs in the table were read separately. Their presence
establishes overlapping implementations, not acceptance or implementation
quality. No tests from those contributions were rerun.

The PWA issue's [US$25 comment](https://github.com/BAWES-Universe/workadventure-universe/issues/3#issuecomment-5636637380)
is a contributor's proposed separate milestone, not a sponsor award. PR397's
stated manifest, icon and HTML-metadata scope already overlaps the requested
work; this does not assert that its browser acceptance checks have passed.

## Proposal-only amount with an existing patch

[tscircuit/file-server#149](https://github.com/tscircuit/file-server/issues/149)
was OPEN, with zero comments and no labels at the observation. Its contributor
supplied a complete patch and regression tests for four file/event consistency
defects, then asked whether the sponsor would consider US$15 or sponsorship
credit. That wording does not establish an approved fixed bounty. Do not spend
another implementation pass on the supplied patch merely because a broad
search surfaced the amount. A subsequent sponsor-funded scope or request for
distinct work is new evidence and should be considered normally.

## Observation checkpoints

The original two records retain `2026-10-04T13:27:36Z`. The PWA and file-server
records use the later `2026-10-04T13:59:10Z` decision checkpoint after their source
reads. These are not repository update times or continuously refreshed status.
Revisit the original issue and PR when circumstances change. Helping an existing
contribution is distinct from starting another same-scope PR.

## Optional reuse

The JSON follows the existing `bountyhub_exclusions` format:

```sh
concierge bountyhub targets retained-catalog.json \
  --exclude-issues docs/discovery/algora-crowded-20261004/exclusions.json
```

This command reduces a retained **BountyHub** catalog offline. It only excludes
these canonical issue identities when they occur in that input; this is not an
Algora catalog collector and does not establish cross-platform listing presence.
Original source links and observation times stay attached to excluded results.
No engine code, shared default, scheduler or provider request is added by these
files. These observations do not change the user's green-platform policy or
minimum bounty amount.

The exact existing validator blob `7839cad2f8325e4baacdc92dced2faf2999a202e`
accepted all four records in one local execution, preserving their evidence
fields. JSON loading used its existing duplicate-field check. Zero provider
calls were made by that validation; no new test suite was added. The original
two records and their observation times are unchanged.

The days-unit issue advertises $50, but this map does not establish funding,
claimant eligibility, a payment route, acceptance or a receivable for any issue.
Do not use it as a financial or sponsor-authorization record.
