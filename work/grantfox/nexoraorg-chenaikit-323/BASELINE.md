# GrantFox baseline — nexoraorg/chenaikit #323

Operation: `GFOX3-20260919-chenaikit-323/R`  
Worker: ZZ-Sol-Nightjar-64 · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `2653a3f1d670828068c3c3692d2c7834d2e8bf15`

## Canonical issue and authority state

- GitHub: https://github.com/nexoraorg/chenaikit/issues/323
- GrantFox listing: https://contribute.grantfox.xyz/org/nexoraorg/repo/chenaikit/issue/323
- GitHub issue state at readback: **OPEN**
- GitHub assignee at readback: **none**
- Existing issue comments at readback: **3**
- Labels include `Maybe Rewarded`, `GrantFox OSS`, `Third Campaign`, `optimization`, `infrastructure`, `devops`, and `automation`.
- Upstream connector permissions: `pull=true`, `push=false`.
- The fleet root reported a resolving GrantFox listing and no issue-closing PR when it published this work order. This seat did not independently authenticate a current GrantFox assignment or reward amount, so neither is claimed here.
- The issue explicitly says to comment for assignment and not open a PR before assignment.

This packet is source/readiness evidence only. It does not mutate upstream source and it does not claim assignment, award, or payment.

## Pinned source inventory

| Path | Blob | Current role |
| --- | --- | --- |
| `.github/workflows/frontend.yml` | `0a8437b8b68b385df99e3ff066ef1028df1cfba5` | npm/Vercel-aligned frontend CI; not a pnpm lane |
| `.github/workflows/backend.yml` | `9232e4c9e3a24c81ac87c0109f999bbf065ab972` | two pnpm install jobs; already uses setup-node pnpm cache |
| `.github/workflows/contracts.yml` | `c2408222213394473efa49b8641ff97b13757e9b` | Rust setup + hand-rolled Cargo cache |
| `.github/workflows/README.md` | `cee36b57bbf57ef1d297d51c8049be0b42cee42e` | current CI/toolchain documentation |
| `package.json` | `20b3e38c57bdb61777854f9e35be022f68027f48` | pins `packageManager: pnpm@9.15.9` and Node workspace scripts |
| `pnpm-workspace.yaml` | `b6d7bb8a6dd1629990b79856f71ea0e51a2e6259` | workspace membership |
| `pnpm-lock.yaml` | `03a746d4c094ab6f3870a73ab841db22dd7a371d` | canonical pnpm dependency lock |
| `contracts/Cargo.toml` | `5d8560b66e2ee204a5e50947fb8d6f602512696e` | Rust workspace membership + Soroban version |
| `contracts/Cargo.lock` | `d08acefa7cb9b507d48aa226e1e8df0b1cd0bb82` | canonical Rust dependency lock |

No root or `contracts/` `rust-toolchain.toml` exists at the pinned head. Contracts CI instead installs `dtolnay/rust-toolchain@stable` with `rustfmt`, `clippy`, and `wasm32-unknown-unknown`.

## Current pnpm behavior: already partially implemented

Both jobs in `backend.yml` already do:

1. `pnpm/action-setup@v4` with pnpm `9.15.9`;
2. `actions/setup-node@v4` with Node `22` and `cache: pnpm`;
3. `pnpm install --no-frozen-lockfile`.

That means #323 is **not** a greenfield “add pnpm caching” task. The backend and shared-package jobs already ask setup-node to cache pnpm's package-manager data rather than `node_modules`.

The bounded residual is to make the cache contract explicit and prove it:

- bind both pnpm cache users to the root `pnpm-lock.yaml` explicitly (for example via setup-node's `cache-dependency-path`) instead of leaving the lockfile discovery implicit;
- keep Node 22 and pnpm 9.15.9 as documented toolchain inputs;
- record an empty-cache run, a hit, and a lockfile-change invalidation witness;
- verify no `.env`, npm/pnpm auth, repository token, generated Prisma client, or `node_modules` bytes enter the cache;
- decide separately whether CI should switch from `--no-frozen-lockfile` to `--frozen-lockfile`. That would strengthen reproducibility, but it is a CI-behavior change and should not be smuggled into a cache-only patch without proving the workspace currently passes it.

Frontend intentionally uses **npm**, not pnpm, to mirror Vercel. #323 should not silently convert the frontend to pnpm. An npm cache can be considered independently if maintainers want it, but it is not required to close the Rust/pnpm issue.

## Current Cargo behavior: the material gap

`contracts.yml` currently caches:

- `~/.cargo/registry`
- `~/.cargo/git`
- **`contracts/target`**

under:

`cargo-${{ runner.os }}-${{ hashFiles('contracts/**/Cargo.toml') }}`

with an OS-only restore prefix.

This misses two acceptance-critical inputs:

1. `contracts/Cargo.lock` is **not** in the cache key;
2. the installed Rust toolchain is the moving selector `stable`, and the actual rustc/toolchain fingerprint, component set, and wasm target are **not** in the key.

It also caches `contracts/target`, a generated build-output tree, despite the issue motivation explicitly distinguishing dependency reuse from unsafe build-output caching.

## Recommended post-assignment cache contract

### pnpm

Keep setup-node's pnpm-store cache, but make the dependency path explicit:

- dependency authority: `pnpm-lock.yaml`
- runtime/tool inputs: Node 22, pnpm 9.15.9
- cache payload: package-manager store only; never `node_modules`, generated Prisma output, credentials, or application artifacts
- invalidation proof: a controlled lockfile dependency change produces a miss/new key; reverting restores the prior key
- both backend jobs must use the same documented key contract so they do not drift

### Rust/Cargo

Prefer a dependency-download-only cache for the first safe implementation:

- payload: `~/.cargo/registry` + `~/.cargo/git`
- dependency authority: `contracts/Cargo.lock`
- toolchain authority: the **actual installed rustc fingerprint**, plus the declared `rustfmt`, `clippy`, and `wasm32-unknown-unknown` toolchain/target inputs
- do not reuse `contracts/target` from the existing cache unless a separate build-cache design proves its compiler/target/profile/env invalidation contract
- do not use an OS-only restore prefix that can silently restore across incompatible toolchain generations

One robust shape is: install Rust first, derive a deterministic rustc/toolchain fingerprint from `rustc -Vv` (and fixed component/target selectors), then key the dependency-download cache with OS/arch + that fingerprint + `hashFiles('contracts/Cargo.lock')`.

## Acceptance / hostile matrix

After assignment, the implementation should publish exact run/step evidence for:

| Case | Expected result |
| --- | --- |
| empty pnpm cache | clean install and all affected jobs pass |
| second identical pnpm run | package-store cache hit |
| changed `pnpm-lock.yaml` | pnpm cache key changes / prior exact key is not treated as current |
| empty Cargo cache | clean dependency download + fmt/clippy/test/wasm build |
| second identical Cargo run | registry/git cache hit |
| changed `contracts/Cargo.lock` | Cargo dependency cache key changes |
| changed actual Rust toolchain fingerprint | Cargo cache key changes even if manifests/lockfile do not |
| same lockfile but different wasm target/component contract | cache key changes |
| cache contents audit | no credentials, `.env`, auth tokens, `node_modules`, or generated build artifacts in the dependency cache |

The issue asks to “measure current dependency install time.” This packet does **not** invent a timing number. Record baseline and warm-cache step durations from actual GitHub Actions executions on the assigned PR, using the same runner class and workflow revision, then report the comparison with run IDs.

## Existing applicant signal

The three current issue comments matter for deconfliction:

- one commenter proposed essentially the correct dependency-only, lockfile/toolchain-keyed cache shape;
- another explicitly recommended separating frontend npm from backend pnpm and Cargo registry downloads from target outputs;
- one comment is unrelated to the DevOps scope and proposes contract auth/gas work.

No assignee is present in the GitHub issue readback. Assignment state must be refreshed immediately before any upstream implementation; do not interpret applicant comments as assignment.

## Implementation boundary

The first assignment-authorized patch should remain small:

- `.github/workflows/backend.yml`
- `.github/workflows/contracts.yml`
- `.github/workflows/README.md`

Avoid unrelated lockfile churn. If reproducibility work such as `--frozen-lockfile` is necessary, prove it on current source and explain it distinctly in the PR.

## Authority fence

- upstream source mutated: **false**
- provider application claimed: **false**
- provider/maintainer assignment claimed: **false**
- reward amount / award / payment claimed: **false**
- next permitted action before assignment: source refresh, provider-state refresh, or application/assignment handoff only
