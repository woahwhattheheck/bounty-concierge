# GrantFox source baseline — nexoraorg/chenaikit #323

Operation: `GFOX3-20260919-chenaikit-323-R-LANTERN`  
Worker: ZZ-Sol-17-Lantern · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `2653a3f1d670828068c3c3692d2c7834d2e8bf15`

## Provider / assignment fence

- Issue: https://github.com/nexoraorg/chenaikit/issues/323
- GrantFox: https://contribute.grantfox.xyz/org/nexoraorg/repo/chenaikit/issue/323
- GitHub issue: OPEN, unassigned, 3 comments.
- GrantFox: Unassigned, Apply visible, one application per user.
- Upstream connector: pull=true, push=false.
- No open #323 PR carrier surfaced.
- Issue explicitly says to get assigned before opening a PR.
- Reward is possible/discretionary only; no award or payment is asserted.

This packet is source-readiness research only and does not mutate upstream.

## Current CI reality

The issue says to "add CI caching for Rust and pnpm dependencies," but both
areas already have caching on pinned main. The residual work is correctness,
scope, and reproducible invalidation.

### Backend / pnpm

Workflow blob:
`9232e4c9e3a24c81ac87c0109f999bbf065ab972`

Current backend and shared-package jobs both:
- install pnpm 9.15.9 with `pnpm/action-setup@v4`;
- use Node 22 with `actions/setup-node@v4`;
- set `cache: pnpm`;
- run `pnpm install --no-frozen-lockfile`.

Exact dependency identity:
- root `pnpm-lock.yaml` blob `03a746d4c094ab6f3870a73ab841db22dd7a371d`;
- `pnpm-workspace.yaml` blob `b6d7bb8a6dd1629990b79856f71ea0e51a2e6259`;
- root `package.json` blob `20b3e38c57bdb61777854f9e35be022f68027f48`.

`setup-node`'s pnpm cache is dependency-store caching, not `node_modules`.
That is the right safety class for this issue. Do not replace it with a broad
workspace cache.

Residual pnpm questions for an assigned implementation:
1. make the dependency-path/key inputs explicit in workflow docs;
2. decide whether `cache-dependency-path: pnpm-lock.yaml` should be explicit
   for readability/future multi-lockfile safety;
3. preserve Node 22 + pnpm 9.15.9 as toolchain inputs in the documented cache
   identity even though setup-node owns the underlying cache key;
4. prove clean miss, hit, and lockfile invalidation with Actions evidence rather
   than inventing cache-hit claims locally.

Because installs use `--no-frozen-lockfile`, cache correctness is not a
substitute for lockfile reproducibility. #323 should not silently broaden into
a frozen-lockfile policy change unless the maintainer asks for it.

### Contracts / Rust

Workflow blob:
`c2408222213394473efa49b8641ff97b13757e9b`

Current Rust setup:
- `dtolnay/rust-toolchain@stable`;
- components `rustfmt, clippy`;
- target `wasm32-unknown-unknown`;
- `RUSTFLAGS="-D warnings"`.

Current cache:
- `~/.cargo/registry`;
- `~/.cargo/git`;
- `contracts/target`;
- key:
  `cargo-${{ runner.os }}-${{ hashFiles('contracts/**/Cargo.toml') }}`;
- restore prefix: `cargo-${{ runner.os }}-`.

Dependency/toolchain identity:
- `contracts/Cargo.lock` blob `d08acefa7cb9b507d48aa226e1e8df0b1cd0bb82`;
- `contracts/Cargo.toml` blob `5d8560b66e2ee204a5e50947fb8d6f602512696e`;
- no root or contracts `rust-toolchain` / `rust-toolchain.toml` exists;
- therefore `stable` is a moving toolchain selector.

The current cache is materially weaker than the issue's acceptance contract:

1. **Cargo.lock is absent from the key.** A transitive dependency update that
   changes only `contracts/Cargo.lock` can restore the same exact cache key.
2. **Rust toolchain identity is absent.** `stable` can advance without any
   repository file changing, while the exact same cache key is restored.
3. **Target/components are absent.** wasm32 target and rustfmt/clippy are part
   of the build environment but not the key.
4. **Build outputs are cached.** `contracts/target` mixes compiled artifacts
   with dependency downloads. The issue explicitly frames the goal as scoped
   dependency caching that preserves reproducibility; registry/git downloads are
   the safer default. If target caching is retained, it requires a much stronger
   compiler/target/profile/source key and explicit justification.
5. **Broad restore prefix weakens exactness.** A miss can restore older entries
   across dependency/toolchain changes. That can be acceptable for registry/git
   download caches, but should not be used to restore build outputs whose ABI
   depends on compiler/target inputs.

## Proposed cache-key matrix after assignment

| Area | Safe cache payload | Required identity inputs | Notes |
| --- | --- | --- | --- |
| pnpm backend/packages | pnpm store only | OS, Node 22, pnpm 9.15.9, `pnpm-lock.yaml` | setup-node already implements the store cache; document/explicit dependency path rather than replacing it |
| Cargo downloads | `~/.cargo/registry`, `~/.cargo/git` | OS, `contracts/Cargo.lock`, Cargo manifests, resolved Rust identity | old restore prefixes may be used only as download warmers |
| Cargo build output (optional) | `contracts/target` | OS, exact rustc/cargo identity, wasm target, profile/RUSTFLAGS, Cargo.lock/manifests, source identity as needed | safest #323 scope is to drop target caching unless measured benefit justifies the stronger key |

### Rust toolchain input problem

A lockfile-keyed cache is not sufficient while the workflow uses moving
`@stable`. An assigned implementation should choose one explicit policy:

- **preferred reproducibility:** add a repository `rust-toolchain.toml` pin
  with required components/wasm target and let both local builds and CI consume
  it; hash that file in the cache key; or
- resolve the exact rustc version during CI and include it in the key before
  cache restore.

Do not claim the key includes "toolchain inputs" while hashing only Cargo.toml.

## Required proof matrix

A complete assigned PR should retain Actions evidence for all of these states:

1. **pnpm clean miss** — no matching store entry; install/build/test succeeds.
2. **pnpm hit** — unchanged lockfile/toolchain restores expected store cache.
3. **pnpm invalidation** — controlled `pnpm-lock.yaml` dependency change yields
   a different exact key/cache provenance.
4. **Cargo clean miss** — empty dependency cache still fmt/clippy/test/wasm-builds.
5. **Cargo hit** — identical lockfile + exact toolchain identity restores only
   the intended paths.
6. **Cargo.lock invalidation** — lockfile-only dependency update changes the key.
7. **toolchain invalidation** — pinned/resolved Rust identity change changes the
   key even if manifests/lockfile do not.
8. **target-input invalidation** — wasm target/profile/compiler changes cannot
   reuse incompatible compiled artifacts.
9. **credential exclusion** — cache path list contains no `.env`, tokens,
   home-level auth files, npmrc with secrets, SSH material, or GitHub credentials.
10. **artifact exclusion** — if `contracts/target` is dropped, prove the cache
    path is download-only; if retained, document the stronger key and reason.
11. **workflow-trigger proof** — the workflow edit itself triggers the relevant
    CI so miss/hit evidence can actually be observed.
12. **docs parity** — `.github/workflows/README.md` names the exact cache
    payloads, key inputs, invalidation behavior, and intentionally uncached data.

GitHub's provider-generated `cache-hit` / setup-node cache logs are the
authoritative hit/miss evidence. A local command cannot prove a hosted Actions
cache hit and should not be presented as one.

## Compatibility / scope notes

- Frontend deliberately uses npm, not pnpm, to mirror Vercel. #323 should not
  convert frontend installs merely to make one cache strategy uniform.
- Backend has two jobs using the same pnpm store semantics; avoid copy-pasting
  conflicting manual caches on top of setup-node.
- The Rust workflow currently uploads wasm artifacts separately. Actions cache
  must not be treated as artifact publication or a substitute for the existing
  upload step.
- Caches are optimization only: every clean-miss run must remain sufficient to
  produce a complete build.
- Avoid caching generated Prisma output, `node_modules`, frontend `dist`,
  wasm release artifacts, secrets, or environment files.

## Provider-ready application angle

A source-specific application can say:

> I pinned current main and found both requested caches already exist, so I
> would harden rather than duplicate them. Backend already uses setup-node's
> pnpm store cache under Node 22/pnpm 9.15.9; I would make the dependency path
> and invalidation contract explicit. Contracts currently cache registry/git
> plus target under a key that hashes Cargo.toml only, so Cargo.lock changes and
> movement of the @stable Rust toolchain can reuse the same key. I would bind
> the cache to Cargo.lock + an exact/pinned toolchain identity and the wasm
> target/components, prefer download-only Cargo caching unless target caching
> shows measured value, document intentionally uncached credentials/build
> artifacts, and prove clean miss, hit, lockfile invalidation, and toolchain
> invalidation in GitHub Actions.

Wait for provider/maintainer assignment before upstream implementation; the
issue explicitly asks contributors not to open unassigned PRs.

## Fleet disposition

- Research: complete.
- Existing caching: YES for pnpm and Rust.
- Residual: cache-key/toolchain/path hardening + documentation + provider-run
  miss/hit/invalidation evidence.
- Implementation: assignment-gated.
- Parallel greenfield "add caches" patch: do not create.
