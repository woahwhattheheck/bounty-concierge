# Publication route gate

`concierge.publication_route_gate` converts a fresh, read-only observation of a bounty repository, provider gate, installed fork, and discovered GitHub write primitives into a deterministic **advisory** publication route.

It exists to stop a finished implementation from dying in a local session and to stop a connector limitation from being misreported as a human-account permission fact. In particular, GitHub's `Resource not accessible by integration` is recorded as **connector access unknown**. It is never treated as proof that the authenticated user lacks repository permission.

## Dispositions

- `DIRECT_BRANCH_PR`: upstream write access, branch/PR primitives, and an actual content-write path were observed.
- `OWNED_FORK_PR`: a writable installed fork owned by the observed actor, an observed parent or fork-network source binding to the declared upstream repository, branch/PR primitives, and an actual content-write path were observed. An actor-owned writable repository with no observed matching fork identity fails closed to `HANDOFF_REQUIRED`.
- `REUSE_EXISTING_PR`: an upstream PR is observed **open** and the provider assignment gate already permits this actor to continue implementation; refresh and continue that carrier. An open PR never overrides an assignment to another actor, unknown assignment state, or an assignment-required wait/application state. Closed, merged, or unknown-state carriers hold instead of silently opening duplicate work.
- `APPLICATION_ONLY`: repository rules require provider assignment and no application has been sent yet.
- `WAIT_ASSIGNMENT`: the application exists but assignment has not arrived.
- `HOLD_ASSIGNED_TO_OTHER`: do not duplicate another assignee's implementation.
- `HOLD_ASSIGNMENT_UNKNOWN` / `HOLD_APPLICATION_UNKNOWN`: refresh provider evidence.
- `HOLD_STALE_OBSERVATION`: refresh all evidence before acting.
- `HANDOFF_REQUIRED`: tested bytes need a worker with a real publication path, or the missing path must be established first.

A content-write path means either `create_file`/`update_file`, or the complete `create_blob` → `create_tree` → `create_commit` → `update_ref` chain. Branch creation plus PR creation alone is not enough to claim tested bytes can be published.

All output receipts pin `target_base_branch` exactly. The gate does **not** silently replace a required `develop` target with `main` merely because `main` is conventional.

## Example: external connector access is unknown

```json
{
  "schema": "publication-route-gate/v1",
  "actor_login": "woahwhattheheck",
  "upstream_repo": "GrantChain/GrantFox",
  "upstream_default_branch": "develop",
  "target_base_branch": "develop",
  "integration_access": "resource_not_accessible",
  "installed_fork_repo": null,
  "installed_fork_push_access": null,
  "installed_fork_parent_repo": null,
  "installed_fork_source_repo": null,
  "publication_primitives": ["create_branch", "create_pull_request"],
  "existing_pr_url": null,
  "existing_pr_state": null,
  "provider_requires_assignment": false,
  "provider_assignment": "none",
  "actor_applied": false,
  "observed_at": "2026-09-19T21:49:00Z",
  "evaluated_at": "2026-09-19T21:50:00Z",
  "max_snapshot_age_seconds": 900
}
```

Run:

```bash
python -m concierge.publication_route_gate observation.json --json
```

With no installed fork, that observation yields `HANDOFF_REQUIRED` and the reason `UPSTREAM_CONNECTOR_ACCESS_UNKNOWN_NOT_PERMISSION_DENIAL`. A fork route becomes `OWNED_FORK_PR` only when a freshly observed `installed_fork_parent_repo` or `installed_fork_source_repo` from repository metadata matches `upstream_repo`; actor ownership and push access alone are not enough. Accepting either identity preserves legitimate nested forks whose immediate parent is an intermediate fork but whose network source is the canonical upstream. Missing both identities yields `INSTALLED_FORK_IDENTITY_NOT_OBSERVED`, while observed identities that do not match the target yield `INSTALLED_FORK_IDENTITY_MISMATCH`.

The compiler performs no network I/O or mutation. A receipt grants no repository, application, assignment, PR, merge, wallet, or payment authority.
