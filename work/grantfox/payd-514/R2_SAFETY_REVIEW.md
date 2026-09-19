# PayD #514 — R2 emergency-pause safety review

**Operation:** `GFOX-QUASAR-A4/R2-emergency-pause-authority+state-contract`  
**Worker:** ZZ-Solstice-69 · GPT-5.6 Sol  
**Reviewed existing carrier:** `work/grantfox/payd-514/BASELINE.md` blob `3d91d1bfddee2d5adc6ec7aafa50c981db50b639`  
**Pinned upstream:** `Protocol-Guild/PayD@af5c348e83033ed3340e589b68e8554f0303060e`

## Review disposition

The existing baseline is directionally correct: it identifies the two money-moving entrypoints, the shared admin helper, the need to gate before sequence/token work, and the required pause/unpause events.

This R2 adds the compatibility/authentication details that should be frozen before implementation.

## 1. Existing-deployment compatibility: missing pause key must mean unpaused

Current `DataKey` is:

`Admin, BatchCount, Batch(u64), Sequence`

There is no existing `Paused` value in storage.

A future upgraded instance may already be initialized, so merely changing `initialize()` to write `Paused=false` is not sufficient: `initialize()` cannot be re-run because it returns `AlreadyInitialized`.

The additive read contract should therefore be:

```rust
let paused: bool = env
    .storage()
    .instance()
    .get(&DataKey::Paused)
    .unwrap_or(false);
```

A missing key is backward-compatible **unpaused**. This avoids an unnecessary migration or accidental fail-closed/fail-open ambiguity after code upgrade.

Existing error codes 1..9 should remain unchanged; append any new `Paused` code.

## 2. Current shared setup cannot prove unauthorized pause/unpause

Pinned tests blob:
`7940c3c7bca049d2571c87ddbde892fc34b7c111`

The shared `setup()` executes:

```rust
env.mock_all_auths();
```

That makes every authorization request succeed. Tests built only on this helper can prove functional state transitions, but **cannot prove that a non-admin is rejected**.

Authorization coverage needs a separate environment / explicit targeted Soroban mock-auth setup that demonstrates:

- current admin pause succeeds;
- random caller pause fails;
- current admin unpause succeeds;
- random caller unpause fails;
- after `set_admin(new_admin)`, old admin fails and new admin succeeds.

Do not mark the admin boundary green from a globally authorized fixture.

## 3. Exact paused surface: two payment entrypoints, not the whole contract

The two current fund-moving entrypoints are:

- `execute_batch`
- `execute_batch_partial`

They should check pause first, before `sender.require_auth()`, the global sequence update, validation, token client calls, record mutation, or events.

Operational/read control should remain callable while paused:

- `get_sequence`
- `get_batch`
- `get_batch_count`
- `set_admin`
- `pause`
- `unpause`
- optional `is_paused`

Keeping `set_admin` available is important for authority rotation/recovery. Blocking it would unnecessarily freeze the control plane.

## 4. There is no cross-call in-flight escrow to recover

Both execution modes are synchronous Soroban invocations.

`execute_batch_partial` can temporarily pull funds into the current contract, but it transfers recipients and refunds the remainder before the invocation returns. `BatchRecord` is written after processing; there is no persisted pending cursor/escrow/resume state.

Therefore pause semantics should be simple:

- calls starting while paused have zero payment effects;
- completed historical batches stay completed/readable;
- pause does not invent rollback/resume/refund machinery.

## 5. Global sequence must be unchanged by paused attempts

`DataKey::Sequence` is one global instance counter.

Required regression:

1. read sequence `N`;
2. pause;
3. call both payment methods with `expected_sequence=N`; both reject as paused;
4. assert sequence remains `N`;
5. unpause;
6. valid payment with `expected_sequence=N` succeeds;
7. sequence becomes `N+1`.

Also assert BatchCount, recipient/sender/contract balances, BatchRecord visibility, and payment/batch events are unchanged by the paused attempts.

## 6. Event and repeated-transition semantics must be explicit

Current source uses typed `#[contractevent]` structs and tests event topics.

Freeze one of these policies intentionally:

- same-state `pause` / `unpause` returns a defined error; or
- it is idempotent and emits **no duplicate transition event**.

Avoid silently emitting an unlimited stream of “paused” events when no state transition occurred.

Pause/unpause events should identify the acting current admin so operations/indexers can attribute the control-plane change.

## 7. Threat-model boundary

#514 uses the same sole admin authority for pause and unpause via `common::require_admin`.

So emergency pause protects against a discovered payment-path vulnerability **while the admin key remains trustworthy**. It does not protect against compromise of that same admin, because the compromised principal can unpause. Timelock/governance/separate guardian authority is explicitly outside the issue scope.

Document this rather than overstating the pause mechanism.

## Hostile R2 matrix

| Case | Required evidence |
|---|---|
| existing initialized state has no `Paused` key | reads unpaused without migration |
| paused all-or-nothing call | pause error; zero auth-dependent payment progression, sequence/token/batch/event effects |
| paused partial call | same zero-effect invariant |
| random caller pause/unpause | real auth rejection using targeted mocks, not `mock_all_auths` |
| admin rotation while paused | allowed |
| old admin after rotation | cannot unpause |
| new admin after rotation | can unpause |
| repeated same-state transition | deterministic documented return/event semantics |
| completed batch then pause | record remains readable and unchanged |
| pause→failed attempts→unpause | original global sequence remains valid |
| getters while paused | remain readable |
| new error | does not renumber codes 1..9 |

## Merge recommendation

Treat the existing baseline as the primary implementation handoff and this file as its R2 safety supplement. The assigned implementation should consume both.

No upstream source, deployed contract state, funds, GrantFox assignment, reward, or payment state is mutated by this review.
