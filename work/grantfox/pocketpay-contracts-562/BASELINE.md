# GrantFox baseline — Axionvera/pocketpay-contracts #562

Operation: `GFOX3-20260919-pocketpay-contracts-562/R-consumer-fixture-source-audit`  
Worker: ZZ-Sol-Halley-83 · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `7988c6efec9a73162ed7d2fffb3b8b6ebd5a7b67`

## Canonical issue / provider fence

- GitHub: https://github.com/Axionvera/pocketpay-contracts/issues/562
- GrantFox: https://contribute.grantfox.xyz/org/Axionvera/repo/pocketpay-contracts/issue/562
- State observed: OPEN
- GitHub assignee: none
- GrantFox public assignment state: **Unassigned**
- GrantFox application route: visible; one application per user
- Existing public comments/applications: 2 generic contributor requests, no GrantFox assignment
- Labels: `expert`, `test`, `sdk`, `contract`, `soroban`, `Maybe Rewarded`, `GrantFox OSS`, `Official Campaign | FWC26`

This packet is current-source evidence and an assignment-ready implementation contract. It does **not** apply, claim assignment, mutate upstream source, or assert a fixed reward.

## Source fence: real residual, narrower than the issue wording

The repository already has a mature *internal* fixture/test layer:

- `contracts/savings_vault/src/test/test_helpers.rs` blob `d05a767400edc940f1e8ba2c622b8ea1bcffa438` provides initialized vault/token/user helpers and deterministic ledger controls.
- `contracts/savings_vault/src/test/event_compatibility.rs` blob `6f50b028159c57b9b57f2af5da40b60bd6d0d477` locks down exact event topics, payload types, and event counts for SDK/indexer compatibility.
- `contracts/savings_vault/src/test/token_transfer_rollback.rs` blob `1d907d6e7f4f18e5794b4868e668a89c84af6fb9` carries deterministic failed-deposit / failed-withdraw / failed-matured-lock atomicity vectors.
- `contracts/savings_vault/src/test/mod.rs` blob `f69f4ff36f4608b2480b1280204f7e63039ab2dc` registers a broad suite including balance, lock, error, event, auth, simulation, and rollback cases.
- `docs/simulation-compatibility.md` blob `ad5d5c97633977465d3deab7334a7dd8ea7a1aa6` explicitly says the existing fixed amounts/timestamps/messages are deterministic so SDK-side tests can assert literal values.
- `docs/codebase-analysis/repo-map-and-workflows.md` blob `00970c7e1592de0d9ebc7b5c961237b6f902cbfc` documents internal `test_snapshots/` as regression fixtures and explicitly says the repo contains no off-chain SDK implementation.
- Code search on pinned main returns no top-level or consumer-facing `fixtures/` corpus.

Therefore #562 should **not** be implemented by adding another set of Rust-only helpers. The remaining acceptance-criteria gap is a stable, portable consumer artifact that SDK/mobile repos can ingest without reproducing Soroban `Env` setup.

## Assignment-ready implementation contract

After provider/maintainer assignment:

1. Add a versioned consumer fixture corpus, preferably under `fixtures/savings-vault/v1/`.
2. Derive fixture values from canonical existing tests/snapshots rather than inventing a parallel behavior specification.
3. Include at minimum:
   - deposit success and invalid/transfer-failure cases,
   - withdrawal success and failure cases,
   - lock creation + matured-lock withdrawal,
   - balance/read-model snapshots,
   - representative typed contract errors,
   - event topics and payloads for the covered state transitions.
4. Make every record deterministic: fixed scenario id, inputs, ledger timestamp, pre-state, expected result/error, post-state, and emitted events where applicable.
5. Use obviously synthetic addresses/identifiers; no seed phrases, private keys, bearer tokens, or user-derived secrets.
6. Document schema/versioning and how SDK/mobile tests consume the corpus.
7. Add a contract-side verification test or generator check that fails when fixture bytes no longer match canonical contract behavior.
8. Treat event/error/schema changes as explicit fixture-version changes rather than silently rewriting historical vectors.

## Hostile / regression coverage

- same fixture generation twice => byte-identical corpus
- deposit and withdrawal custody deltas reconcile with internal balances
- failed token transfer => unchanged accounting + no success event
- matured lock and immature lock are distinguishable at the boundary timestamp
- error fixture binds stable typed error code/category where the contract exposes one
- event fixture asserts exact topic order + payload shape
- fixture corpus contains no secret-like material
- consumer parser rejects unknown schema major versions
- stale fixture detection trips when canonical test vector changes

## Suggested verification

- `cargo test --workspace`
- targeted fixture/generator verification command introduced by the implementation
- JSON/schema validation for every exported fixture
- a tiny consumer smoke test that parses the corpus without Soroban testutils

Report exact exit codes; do not claim hosted CI green when it is merely queued.

## Application draft

> Applying for #562 after auditing current `main@7988c6efec9a73162ed7d2fffb3b8b6ebd5a7b67`. The repo already has strong deterministic internal fixtures in `test_helpers.rs`, event compatibility tests, token-transfer rollback vectors, simulation compatibility cases, and `test_snapshots/`. The source-real residual is not “add more Rust test helpers”; it is a portable, versioned consumer corpus that SDK/mobile repos can ingest without reproducing Soroban `Env` setup. After assignment I would export canonical deposit/withdraw/lock/balance/error/event scenarios, bind them to existing tests, add schema/version + no-secret guarantees, and add drift verification so contract behavior cannot silently diverge from consumer fixtures.

## Authority / reward boundary

`Maybe Rewarded` and campaign labels indicate campaign context only. No fixed payout, award, or payment is asserted. Upstream implementation remains gated on provider or maintainer assignment.
