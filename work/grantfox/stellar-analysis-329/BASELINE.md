# Stellar-Analysis/frontend #329 — request-signing replay-protection source fence

## Disposition

**SOURCE_DRIFT / CANONICAL BACKEND ABSENT — do not implement against current `main` without maintainer direction.**

This packet is pre-assignment source research only. It does **not** apply for, claim, assign, implement, submit, adjudicate, or assert payment for the GrantFox issue.

## Provider / issue snapshot

- Upstream: `Stellar-Analysis/frontend#329`, “Request signing with nonce-based replay protection”.
- GitHub issue state: **OPEN**.
- GitHub comments: **0**.
- GitHub assignees: none observed.
- Labels observed: `GrantFox OSS`, `Maybe Rewarded`, `Third Campaign`.
- Targeted open-PR search for `#329`: no matching open carrier observed.
- Connector repo metadata: `pull=true`, `push=false`.
- Collaborator-permission probe returned `403 Resource not accessible by integration`; that is a connector-scope result, **not** a claim about the human GitHub account.

## Canonical source pin

Current canonical `main` is pinned at:

`482ee456369418ef82c4056718cb82d3468f762b`

Evidence on that tree:

- `README.md` blob `a7546f7a6f85709fccd21e136cd8b12bae820652` describes the repository as the Next.js/React frontend and documents only the frontend `src/**` layout.
- `package.json` blob `2b1c6ac1f83096666c7fd6d5ba3fd22780e6b8eb` is a Next.js/Vitest frontend package and has no Redis client dependency.
- `pnpm-lock.yaml` blob `7ecba249d1b7cd41e629e2fff2782ed6805186f5` is the canonical JS lockfile.
- `backend/Cargo.toml`: **absent (404)**.
- `backend/src/lib.rs`: **absent (404)**.
- root `Cargo.toml`: **absent (404)**.
- Current-tree searches surfaced no `request_signing`, HMAC, nonce, or Redis substrate.

The issue, however, prescribes a Rust layout:

```
backend/request_signing/
  mod.rs
  verify.rs
  nonce_store.rs
backend/tests/
  replay_within_window_test.rs
  clock_skew_test.rs
```

That target does not exist on canonical main.

## Why this is source drift, not a greenfield invitation

A historical ancestor `d8bae439cbaeb0f6b3a4fb36ffe212993c3af617` did contain the Rust backend. Its `backend/Cargo.toml` blob is `5f15f0b4c473a688ff5a42767c4ad9abdb477b8d`.

GitHub comparison from that ancestor to canonical `main@482ee456...` reports main **41 commits ahead** and explicitly reports the backend as removed, including:

- `backend/Cargo.toml`
- `backend/Cargo.lock`
- `backend/src/lib.rs`
- distributed-lock, network, realtime, reconciliation, replay, snapshot, and test modules.

Open PR #386 is based on that older backend lineage (`base=d8bae439...`) and its head carries Redis `0.27`, but an unmerged PR/ancestor is not a valid canonical dependency for #329. Building request signing on that lineage without maintainer confirmation would couple the bounty to code intentionally absent from current main.

## Maintainer contract required before assignment-time implementation

Resolve these questions first:

1. **Canonical target:** Was the Rust backend intentionally removed/relocated? Which branch or repository is authoritative for #329?
2. **Dependency order:** Is #329 blocked on restoring/merging a backend substrate? If an open backend PR is the intended base, name the exact prerequisite PR/commit.
3. **Signing scheme:** HMAC or asymmetric/keypair verification? Define key lookup/rotation and a versioned wire format.
4. **Canonical signed bytes:** Bind at least version, credential/key id, timestamp, nonce, HTTP method, canonical path/query, and the exact body (or a specified body digest). Signing only “timestamp + body” permits a valid signature to be replayed across endpoints that accept the same payload.
5. **Clock policy:** Define whether future client timestamps are accepted and specify inclusive/exclusive boundary behavior.
6. **Nonce scope:** Define whether nonce uniqueness is global or credential-scoped. Credential/key-id scoping avoids accidental/global collision DoS while still preventing same-credential replay.
7. **Failure semantics:** Pin typed outcomes for malformed signature, unknown key, expired/not-yet-valid timestamp, duplicate nonce, and nonce-store unavailable.

## Replay-store invariant

The nonce claim must be **atomic** across replicas. A check-then-set sequence is insufficient. Redis should use one atomic claim (for example, `SET key value NX PX/EX ...` or a reviewed Lua operation) with a namespaced key such as `request-signing:<key-id>:<nonce>`.

### Important correction to the issue's TTL wording

“TTL exactly matched to timestamp tolerance” is safe only under a compatible time policy.

If verification accepts a **symmetric** window `abs(now - signed_at) <= W`, a request may first be accepted as early as `signed_at - W` and remain signature-valid until `signed_at + W`. A nonce TTL of merely `W` from first use can therefore expire while that same signed request is still valid.

Use one of these explicit designs:

- **One-sided age policy:** reject future timestamps and accept only `0 <= now - signed_at <= W`; then a `W` nonce TTL from first accepted use covers the remaining validity horizon.
- **Symmetric skew policy:** expire the nonce no earlier than the signed request's final acceptance instant, e.g. TTL = `signed_at + W - now` at first acceptance (bounded and positive), or use an intentionally conservative longer TTL.

Whichever policy is chosen must be documented and boundary-tested.

## Assignment-time acceptance matrix

Once the canonical backend is resolved:

- valid signature + fresh timestamp + first atomic nonce claim => accepted;
- same exact request/nonce a second time within its signature-valid horizon => rejected;
- concurrent same-nonce submissions to two replicas => exactly one atomic claim succeeds;
- request just inside and just outside every timestamp boundary => deterministic expected result;
- future timestamp behavior => explicit test, not accidental library behavior;
- signature over body A with body B => rejected;
- same signed body replayed to a different method/path => rejected because method/path are bound;
- nonce-store outage => fail closed (no “accept because Redis is unavailable” fallback);
- nonce-store TTL covers the entire remaining signature-valid horizon;
- secrets/signing keys never appear in logs or error bodies;
- tests use deterministic clocks and an explicit shared-store abstraction.

## Recommended next action

Do **not** spend the one-user application/assignment slot on a guessed architecture. Ask the maintainer to name the canonical backend target/dependency and clock/signature policy. Once that is explicit, this becomes a strong security implementation lane rather than a speculative resurrection of removed code.
