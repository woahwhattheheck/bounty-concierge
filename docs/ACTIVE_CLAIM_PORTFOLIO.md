# Active Claim Portfolio

`concierge.active_claim_portfolio` is an offline internal-custody boundary between paid-work selection and PR closeout. It answers one operational question: **given exact upstream bounty evidence plus an immutable internal event ledger, which opportunities are already owned, which are safe to assign internally, and which must hold because custody, evidence, age, or operator capacity is unsafe?**

It does **not** claim a bounty on GitHub. A `READY_FOR_INTERNAL_CLAIM` result authorizes only an internal operator assignment. The module never contacts a sponsor or maintainer, submits work, mutates a wallet/provider, converts currencies, infers acceptance/payout/cash, or recognizes revenue.

## Inputs

### Candidate

Each candidate is an exact JSON object with:

- `repo`, `number`: canonical GitHub issue identity;
- `sponsor_key`: bounded operator-defined grouping key used only for capacity policy;
- `worker_id`: intended internal worker for capacity evaluation;
- `observed_at`: canonical whole-second UTC capture time;
- `reward_currency`, `reward_minor`: native-currency reference metadata only; there is no FX or portfolio reward sum;
- `qualification`: the exact upstream qualification receipt. It must say `disposition=ACTIONABLE` and `dispatch=true`;
- `availability`: the exact `bounty-availability/v1` receipt. It must match repo/issue and say `disposition=CLEAR`, `dispatch=true`.

The opportunity-generation digest binds repo/issue, sponsor key, observation time, native reward metadata, and SHA-256 commitments to the full qualification and availability receipts. `worker_id` is intentionally *not* part of that generation: changing the intended worker must not pretend the external opportunity itself changed.

Exact candidate replay collapses. Distinct candidate variants for one repo/issue HOLD because two internal assignments cannot both be authoritative.

### Internal claim events

Events are append-only and contain:

- immutable `event_id`;
- exact repo/issue, sponsor key, worker ID, and opportunity-generation digest;
- state `CLAIMED`, `RELEASED`, or `COMPLETED_INTERNAL`;
- canonical `event_at` UTC;
- predecessor event ID + digest for a terminal transition;
- bounded evidence reference + SHA-256.

A generation has exactly one root `CLAIMED` event. The only valid transition is from that root to one `RELEASED` or `COMPLETED_INTERNAL` event by the same worker/sponsor, with an exact predecessor digest. Exact replay is idempotent. Changed event-ID reuse, multiple roots, predecessor gaps/tamper, worker/sponsor drift, future evidence, state regression, or multiple active generations HOLD.

`COMPLETED_INTERNAL` means only that the internal lane is finished/released. It is explicitly **not** maintainer acceptance, merge, payout, cash, or revenue evidence.

### Capacity policy

The exact versioned policy supplies integer limits:

- `max_active_claims_total`;
- `max_active_claims_per_worker`;
- `max_active_claims_per_sponsor`;
- optional `sponsor_overrides`;
- `max_claim_age_seconds`.

There is no learned score. An old-generation claim that is still active continues consuming capacity until it is released; otherwise stale custody could disappear from scheduling and overbook the swarm. If any event lineage is ambiguous enough that active capacity cannot be known authoritatively, the receipt marks `ledger_capacity_uncertain=true` and refuses to emit unrelated new `READY_FOR_INTERNAL_CLAIM` rows until the conflict is resolved.

## Dispositions

- `ACTIVE_OWNED`: one current, non-stale internal claim exists.
- `READY_FOR_INTERNAL_CLAIM`: upstream receipts are dispatchable and explicit capacity remains.
- `CAPACITY_HOLD`: total, worker, or sponsor capacity is exhausted.
- `STALE_CLAIM_REVIEW`: active custody reached the configured review age.
- `UPSTREAM_CHANGED_HOLD`: an active claim binds an older opportunity generation.
- `CONFLICT_HOLD`: candidate or event custody is ambiguous, malformed, conflicting, or non-dispatchable.

At the exact `max_claim_age_seconds` boundary the claim moves to `STALE_CLAIM_REVIEW`.

## Canonical receipt and verification

The compiler emits `active-claim-portfolio/v1` with:

- exact trusted `as_of`;
- normalized policy + policy SHA-256;
- input-set SHA-256 commitments;
- active-capacity counts partitioned by worker and sponsor;
- deterministic per-opportunity dispositions;
- explicit ledger conflict evidence;
- a hardcoded authority ceiling;
- whole-packet `receipt_sha256`.

The verifier recompiles from the exact candidates, events, policy, and trusted `as_of`. Any receipt, source, policy, or time drift fails verification.

JSON CLI reads reject duplicate object keys, floats, unsafe integers, over-large/non-regular inputs, and noncanonical UTC. Compilation writes create-exclusively and refuses existing/symlink final targets.

## CLI

```bash
python -m concierge.active_claim_portfolio compile \
  --candidates candidates.json \
  --events events.json \
  --policy policy.json \
  --as-of 2026-09-13T12:00:00Z \
  --output receipt.json

python -m concierge.active_claim_portfolio verify \
  --candidates candidates.json \
  --events events.json \
  --policy policy.json \
  --as-of 2026-09-13T12:00:00Z \
  --receipt receipt.json
```

The compiler and verifier are offline. Upstream live GitHub acquisition remains the job of the existing availability/qualification path.
