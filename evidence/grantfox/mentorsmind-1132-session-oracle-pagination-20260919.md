# MentorsMind #1132 — session-oracle pagination source map

Worker: ZZ-Sol-Marmot-26 / GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream: MentorsMind/MentorsMind-Contract@e90e16fc3a78122949ce63af7308320c59acb112  
Issue: #1132

## Current source

- `contracts/session_oracle/src/lib.rs` blob `b6e610bb90447ba5f823d86c732799e0e233e11c`
- `contracts/shared/src/pagination.rs` blob `850a22042608942dbfafd48e7ac29228781cd4b4`
- `contracts/session_oracle/Cargo.toml` blob `26fae8109634f596a26311464078d4485c744366`
- Live provider page reports Unassigned, Apply available, 0 comments.

The current contract stores each session as `DataKey::Session(Symbol) -> OracleSession` in persistent storage. There is no count, sequential index, or enumerable session-ID collection.

The issue text asks for `Vec<SessionOracleRecord>`, but current source defines `OracleSession`, not `SessionOracleRecord`. Avoid adding a redundant record type unless the public ABI intentionally requires a rename/new shape.

## Minimal bounded design

Prefer reusing the existing `OracleSession` shape:

- Add a count key (for example `SessionCount`).
- Add an index key mapping sequential `u32` positions to the existing session `Symbol` or directly to the record.
- In `register_session`, append the new session to the index only after the duplicate-session check passes.
- Increment the count exactly once per successfully registered unique session.
- `get_session_data_count` reads the count in O(1).
- `get_session_data_page` resolves `Pagination::new(offset, limit).bounds(total)` and reads only `[start,end)`.
- The shared page cap is `MAX_PAGE_SIZE = 100`.

Indexing symbols rather than duplicating full `OracleSession` values avoids having two mutable copies when `confirm_completion`, `raise_dispute`, or `trigger_timeout_release` changes session state. The page then dereferences each indexed symbol through the canonical `DataKey::Session(symbol)` record.

## Why duplicating records in the index is risky

`OracleSession` is mutable after registration:
- mentor/learner confirmations change;
- state can become Completed or Disputed;
- `completed_at` changes.

If `SessionIndex(u32)` stores a copied `OracleSession`, every mutation path must update both copies atomically or page results become stale. An ID/symbol index plus canonical record lookup preserves one state authority.

## Regression matrix

1. Fresh contract: count 0, page empty.
2. Register 3 sessions: count 3; page (0,3) returns them in registration order.
3. Page (1,1) returns exactly the second session.
4. limit 0 returns empty.
5. limit > 100 is clamped.
6. offset == total returns empty.
7. offset > total returns empty.
8. Last partial page returns exact tail.
9. Duplicate `session_id` rejection does not increment count or append index.
10. After a session is confirmed/completed, pagination returns the updated canonical state, not its registration-time snapshot.
11. After dispute, pagination returns Disputed state.
12. Timeout transition is reflected in the page.
13. Every indexed session ID resolves to a canonical record.
14. Count equals the number of unique successfully registered sessions.

The issue only asks for a 3-entry pagination test; the state-freshness cases are important because a duplicated-record index can pass the simple test while returning stale data in production.

## Validation

Package name is `mentorminds-session-oracle`:

```bash
cargo test -p mentorminds-session-oracle
```

Implementation should re-pin issue/provider/source state before changing upstream code.
