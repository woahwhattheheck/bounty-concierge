# Head-first work-order lease reconciliation

Use `concierge.work_order_lease.reconcile_tracked_head` only at a **recovery,
collision, or publication boundary** after the caller has already made one fresh
provider head read. It performs no network I/O and must not be put in a polling
loop.

The caller supplies the tracked repository/branch, the work order's
`expected_head`, the fresh `observed_head`, the claimed source-path scope, and
the touched paths from one `expected_head..observed_head` comparison. The receipt
retains the operation ID, repo, branch, expected and observed heads, normalized
touched/claimed paths, owner/source identity, and the caller's provider readback.

## Decisions

- **UNCHANGED_STALE** — the branch has not moved. Keep the stale lease; no extra
  provider reads are justified by this helper.
- **CLAIMED_SCOPE_COMPLETE** — the new head changed exactly the claimed scope.
  Emit collision reconciliation and tombstone duplicate work instead of
  assigning/rebuilding it.
- **CLAIMED_SCOPE_OVERLAP** — the new head overlaps the claimed scope plus other
  paths. Fail closed the duplicate lease and reconcile ownership before doing
  source work.
- **ORTHOGONAL_ADVANCE** — the head moved only outside the claimed scope. Rebase
  the lease's expected-head fence to the observed head and continue with CAS.
- **MISSING_COMPARE** — the head moved but no touched-path compare was supplied.
  Hold rather than guessing.

Claimed paths are exact repository-relative paths. A claimed directory may be
written as `path/to/dir/` or `path/to/dir/**`; either form matches descendants.

## Example

```python
from concierge.work_order_lease import reconcile_tracked_head

receipt = reconcile_tracked_head(
    operation_id="IH-CSS396-PR1488",
    repo="owner/repo",
    branch="fix/bounty",
    expected_head="a" * 40,
    observed_head="b" * 40,
    claimed_paths=["src/service/", "tests/test_service.py"],
    touched_paths=["docs/notes.md"],
    owner="publisher-seat-7",
    source="slack-work-order-1791441921",
    provider_readback={"provider": "github", "compare": "a..b"},
)
assert receipt["action"] == "REBASE_EXPECTED_HEAD"
```

The receipt is deterministic and includes a SHA-256 over its unsigned fields so
dispatchers can retain it as a durable reconciliation record.

## Boundaries

- No GitHub or Slack calls are made by the helper.
- No source, PR, claim, payout, or wallet mutation is authorized.
- Use exactly one fresh head read and one compare when a boundary requires it;
  do not continuously poll healthy branches.
- The caller remains responsible for the next expected-head CAS before any
  source or publication write.
