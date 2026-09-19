# MentorsMind #1125 — grants pagination source map

Worker: ZZ-Sol-Marmot-26 / GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream: MentorsMind/MentorsMind-Contract@e90e16fc3a78122949ce63af7308320c59acb112  
Issue: #1125

## Current source

- `contracts/grants/src/lib.rs` blob `2c3d17c614ee7e66827e9a480b7a6704a9aea21d`
- `contracts/shared/src/pagination.rs` blob `850a22042608942dbfafd48e7ac29228781cd4b4`
- `contracts/grants/Cargo.toml` blob `6492bf293ead0b95fc2a8bb7ad38a59dbbedcfa1`
- Issue is open, unassigned, 0 comments; no matching PR was found.
- Current public issue page exposes an Apply action and reports Unassigned.

Repository-wide current-main searches found no `GrantRecord`, `GrantIndex`, `RecipientGrants`, `get_grants_page`, or `get_grants_by_recipient`.

## Schema gap

Current grants storage has two concepts:

1. `GrantProgram(u32) -> GrantProgram`, counted by `ProgramCount`.
2. `GrantAllocation(Address, u32) -> i128`, keyed by learner plus program.

There is no enumerable allocation-history collection or global allocation identifier. The requested `Vec<GrantRecord>`, sequential `GrantIndex(u32)`, and `RecipientGrants(Address) -> Vec<u32>` therefore imply new persistent schema, not only new view functions.

Before implementation, bind what a grant record means:

- whether a record is created at application, approval, or another transition;
- whether recipient means the current `learner` argument to `approve_grant`;
- the record fields and global identifier;
- whether records are immutable history;
- whether `get_grant_count` counts allocation records or programs (programs already have `get_program_count`);
- how the issue description's status-listing goal maps to source, because current grants state has no grant-status type and the listed acceptance functions contain no status filter.

Do not silently invent those semantics.

## Bounded implementation shape once semantics are fixed

If a record represents a successful approved allocation:

- use a history count separate from `ProgramCount`;
- map sequential global IDs to `GrantRecord`;
- maintain each recipient's vector of global IDs;
- append exactly once after all approval validation passes;
- preserve the existing duplicate-approval guard so a second attempt cannot append;
- global paging uses `Pagination::new(offset, limit).bounds(total)`;
- recipient paging resolves bounds against that recipient vector length and reads only selected IDs;
- both paths return at most `MAX_PAGE_SIZE`, currently 100;
- never implement recipient filtering by scanning the whole global collection.

## Regression matrix

1. Empty collection gives count 0 and empty page.
2. Single record gives count 1 and exact page.
3. Global order is stable by sequential ID.
4. Recipient page contains only matching-recipient records.
5. limit 0 gives empty page.
6. Oversized limit returns at most 100.
7. offset == total gives empty page.
8. offset > total gives empty page.
9. Last partial page returns exact tail without panic.
10. Same recipient across multiple programs is indexed correctly.
11. Two recipients in one program remain independently indexed.
12. Rejected approvals do not change count or indexes.
13. Duplicate approval after one success does not append a second record.
14. Every recipient-index ID resolves to a record with that recipient.
15. Global count matches the number of created global index entries.

## Existing test-baseline concern

Current `get_token_balance` is a placeholder that always returns zero. `check_grant_commitment` therefore computes a zero grant ceiling and appears to reject any positive new program budget, while current unit tests create positive-budget programs.

Run the existing package tests before attributing failures to #1125. Keep any pre-existing grants/treasury-fixture failure separate from the pagination change unless maintainers explicitly include that prerequisite.

Package test command:

```bash
cargo test -p grants
```
