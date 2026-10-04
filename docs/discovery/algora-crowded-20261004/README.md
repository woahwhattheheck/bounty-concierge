# Reuse two crowded-bounty observations

These optional exclusions record why the October 4 discovery pass did not start
another implementation of two issues surfaced by Algora-related searches.
They are not global defaults, permanent bans, or a new approval queue.

| Issue | Directly read matching PR | Observed state and head |
| --- | --- | --- |
| [Days-unit parsing #1](https://github.com/tine1117/oss-hunter-livefire/issues/1) | [PR162](https://github.com/tine1117/oss-hunter-livefire/pull/162) | OPEN, unmerged; `0198218990acdc3382c96960e9e02a14afbeb3bc` |
| [PGlite type generation #2](https://github.com/seveibar/pgstrap/issues/2) | [PR86](https://github.com/seveibar/pgstrap/pull/86) | OPEN, unmerged; `c03496b821b6abcabf39034f74d0848632e8fb86` |

The open-PR searches additionally returned days-unit PRs161/160/159/158 and
PGlite PRs85/84. Those search results are not a full enumeration or individual
source reviews. The two exact PRs above were read separately. Their presence
establishes overlapping implementations, not acceptance or implementation
quality. No tests from those contributions were rerun.

`observed_at` retains the 13:27:36 UTC observation checkpoint, following the
source reads. It is not a repository update time or a continuously refreshed
status. Revisit the original issue and PR when circumstances change. Helping an
existing contribution is distinct from starting another same-scope PR.

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
No code, shared default, scheduler or provider request is added by these files.

The exact existing validator blob `7839cad2f8325e4baacdc92dced2faf2999a202e`
accepted both records in one local execution, preserving their evidence fields;
zero provider calls were made by that validation. No new test suite was added.

The days-unit issue advertises $50, but this observation does not establish
funding, claimant eligibility, a payment route, acceptance or a receivable for
either issue. Do not use this map as a financial or sponsor-authorization record.
