# Atomic Git-ref outbound custody

Use this as the **preferred cross-session serialization authority for cooperative
revenue-bearing outbound writers**. Slack, issue search, and other indexed feeds remain useful audit/mirror
surfaces, but they are not authorization: an acknowledged post can take time to
appear in search, which is enough for two cloud seats to believe they both won.

`concierge.outbound_gitref.GitHubRefClaimStore` uses GitHub's ref mutation rules
instead. The operation gets one deterministic branch:

```text
outbound-claims/<64-char SHA-256 operation key>
```

Only the key appears in the ref; recipient/destination data does not.

## Why the CAS is stronger

The first claimant creates a candidate state commit and tries to create the
operation ref. Ref creation is the serialization point: only one create can win.

Every later transition creates a commit whose parent is the exact currently
observed claim commit, then advances the branch with `force=false`. Two contenders
starting from the same parent create sibling commits. Once one sibling advances the
ref, the other is no longer a fast-forward and must fail closed.

The state machine is:

```text
(absent) -> CLAIMED -> SENT
                   \-> RELEASED -> CLAIMED -> ...
```

There is **no timeout transition**. A crashed owner may have sent successfully, so
time passing is never proof that another send is safe.

Each `CLAIMED` generation carries a random `claim_id`. `release()` and
`mark_sent()` require that exact generation token. A stale process from an older
claim therefore cannot finalize or release a newer owner's claim even when a seat
name is reused.

## Required send sequence

1. Derive the operation key with `outbound_operation_key()` using the same
   provider, canonical destination, stable lead/thread id, and stable business
   operation across every seat.
2. Call `GitHubRefClaimStore.claim(key, owner)`. If it reports busy, sent,
   conflict, corruption, GitHub unavailability, or any other uncertainty: **do not
   send**.
3. Optionally mirror `CLAIMED key=<K> owner=<seat> claim_id=<id>` to Slack for
   observability. Slack is not the lock.
4. Acquire the local `OutboundSingleWriter` lease. This is still required to stop
   two processes in the winning seat/runtime.
5. Perform all non-mutating preflight reads.
6. Call local `prepare_send()` immediately before the external provider mutation.
7. Perform exactly one provider send.
8. On a positive provider receipt, call shared `mark_sent()` **before** releasing
   any custody, then local `finalize_sent()`. Mirror terminal SENT to Slack if
   useful.
9. If the send result is uncertain, leave both authorities blocking. Do not retry.
   Reconcile the provider. Only authoritative proof of *no send* permits local
   `reconcile_not_sent()` plus shared `release(..., evidence_reference=...)`.
10. A voluntary handoff is allowed only before any external send could have
   happened. Release the local HELD lease and the shared claim explicitly.

## Python usage

```python
import os

from concierge.outbound_gitref import GitHubRefClaimStore
from concierge.outbound_singlewriter import OutboundSingleWriter, outbound_operation_key

key = outbound_operation_key(
    provider="gmail",
    destination="buyer@example.com",
    thread="lead:buyer-123",
    operation="proposal-followup-1",
)

shared = GitHubRefClaimStore(
    "woahwhattheheck/bounty-concierge",
    token=os.environ["GITHUB_TOKEN"],
)
claim = shared.claim(key, "Z-example-seat")

local = OutboundSingleWriter("/secure/local/outbound")
held = local.acquire(
    provider="gmail",
    destination="buyer@example.com",
    thread="lead:buyer-123",
    operation="proposal-followup-1",
    owner="Z-example-seat",
)
permit = local.prepare_send(
    operation_key=key,
    lease_id=held["lease_id"],
    owner="Z-example-seat",
)

# Perform exactly one external send here.
provider_receipt = "opaque-provider-message-id"

shared.mark_sent(key, "Z-example-seat", claim.state.claim_id, provider_receipt)
local.finalize_sent(
    operation_key=key,
    permit_id=permit["permit_id"],
    owner="Z-example-seat",
    provider_receipt_id=provider_receipt,
)
```

The token needs permission to read commits/refs and create/update refs in the
chosen coordination repository. Treat token/permission/provider failures as a hard
block, not as permission to fall back to search-based election.

## Connector / manual GitHub equivalent

A seat using a GitHub connector rather than a raw token can apply the same
protocol:

- create a state commit with the repository's current tree and a canonical
  `OUTBOUND_GITREF_STATE_V1` message;
- for an absent operation, atomically create branch
  `outbound-claims/<K>` at that candidate commit;
- if the branch already exists, read its target state and obey it;
- for RELEASED/SENT/next-CLAIM transitions, create a child of the exact current
  claim commit and move the branch with a **non-force** ref update;
- any conflict/non-fast-forward/error is a hard block.

Do not use force updates, branch deletion, timeout expiry, or "I don't see it in
search" as an authorization path.

### Repository-administrator ceiling

The CAS is linearizable only while writers obey non-force updates and nobody with
repository administration authority rewrites or deletes the claim refs. GitHub
admins can otherwise defeat the mutex. Protect `outbound-claims/*` with a ruleset
that blocks deletion and non-fast-forward updates when administration tooling is
available. At publication time this repository exposes no ruleset, so treat this
as a strong cooperative-writer guard, **not** protection from a malicious or buggy
repo administrator.

## State and privacy boundary

Claim commits contain only:

- operation key;
- state (`CLAIMED`, `RELEASED`, or terminal `SENT`);
- seat owner id;
- per-generation `claim_id`;
- monotonic sequence;
- optional opaque provider receipt or reconciliation evidence reference.

They do not contain the destination, message body, credentials, API token, price,
buyer acceptance, payment status, or revenue recognition. A SENT claim proves only
that the coordinating sender recorded a provider receipt for that outbound
operation; it does not prove a buyer accepted, paid, or owes money.
