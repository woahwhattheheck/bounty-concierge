# GrantFox source contract — nexoraorg/chenaikit #323 CI dependency caching

Work ID: **GFOX3-20260919-chenaikit-323/R-cache-key+workflow-source-contract**  
Owner/session: **ZZ–Solstice / GPT-5.6 Sol**  
Census date: 2026-09-19 EDT  
Issue: https://github.com/nexoraorg/chenaikit/issues/323  
GrantFox: https://contribute.grantfox.xyz/org/nexoraorg/repo/chenaikit/issue/323  
Upstream source pin: `main@2653a3f1d670828068c3c3692d2c7834d2e8bf15`  
Upstream tree: `e582ce7b45c7fe2f8c23a6a44327df42494af504`  
Slack TAKE: https://tokenjunkielabs.slack.com/archives/C0BVANHNB26/p1789858254611899

## Authority / provider fence

At source-census time:

- GitHub issue #323 is OPEN and unassigned.
- GrantFox renders **Assigned to: Unassigned**, **Apply to this issue**, and **1 application per user · Direct GitHub comment**.
- Three prior applicant comments are visible.
- The issue explicitly says: **comment on the issue to get assigned; please don't open a PR for an unassigned issue**.
- A collaborator-permission probe for the connected GitHub installation returned `403 Resource not accessible by integration`; this packet does not infer upstream write authority from that failure.
- No upstream source or workflow mutation is authorized by this packet.
- No GrantFox reward or assignment is claimed.

This is source/readiness work only. Recheck assignment, provider state, upstream main, issue comments, and open PRs immediately before implementation.

## Issue premise versus current source

The issue asks to:

1. measure dependency install time;
2. add lockfile-keyed caches;
3. document invalidation;
4. keep cache miss reproducible;
5. avoid caching credentials or unsafe build outputs;
6. prove miss, hit, and dependency-change invalidation.

Current source is **partially ahead of the issue premise**.

### Backend

`.github/workflows/backend.yml` already configures:

- `pnpm/action-setup@v4` at pnpm `9.15.9`;
- Node `22` via `actions/setup-node@v4`;
- `cache: pnpm` in both backend and shared-packages jobs;
- root `pnpm install --no-frozen-lockfile`.

So “add pnpm caching” is not a greenfield task. An assigned repair should first verify whether the built-in setup-node cache satisfies the issue's requested key/invalidation contract and should not add a redundant second pnpm cache.

### Contracts

`.github/workflows/contracts.yml` already has a Cargo cache, but its current contract conflicts with the issue's stated safety/reproducibility goal:

- cached paths:
  - `~/.cargo/registry`
  - `~/.cargo/git`
  - **`contracts/target`**
- primary key:
  - `cargo-${{ runner.os }}-${{ hashFiles('contracts/**/Cargo.toml') }}`
- broad restore prefix:
  - `cargo-${{ runner.os }}-`

The repository has a committed `contracts/Cargo.lock`, but the cache key does **not** include it.

The workflow resolves `dtolnay/rust-toolchain@stable` dynamically and does not pin a `rust-toolchain` or `rust-toolchain.toml` file. Therefore the primary cache key also does not identify the actual compiler/toolchain resolved for the run.

Most importantly, the cache includes `contracts/target`, which is compiled build output. The issue motivation explicitly says dependency downloads should be reused **without caching build outputs unsafely**. The current contract should therefore be treated as a repair target rather than an example to duplicate.

### Frontend

`.github/workflows/frontend.yml` uses:

- Node `22`;
- npm, deliberately, to match Vercel;
- `npm install --no-audit --no-fund --legacy-peer-deps`.

It has **no dependency cache configured**.

The repo contains a frontend-specific lockfile:

- `apps/frontend/package-lock.json`

This frontend npm lock regime is distinct from the root pnpm workspace.

## Exact relevant blobs

Upstream `main@2653a3f1d670828068c3c3692d2c7834d2e8bf15`:

- `.github/workflows/frontend.yml` — `0a8437b8b68b385df99e3ff066ef1028df1cfba5`
- `.github/workflows/backend.yml` — `9232e4c9e3a24c81ac87c0109f999bbf065ab972`
- `.github/workflows/contracts.yml` — `c2408222213394473efa49b8641ff97b13757e9b`
- `.github/workflows/README.md` — `cee36b57bbf57ef1d297d51c8049be0b42cee42e`
- `pnpm-lock.yaml` — `03a746d4c094ab6f3870a73ab841db22dd7a371d`
- `pnpm-workspace.yaml` — `b6d7bb8a6dd1629990b79856f71ea0e51a2e6259`
- root `package.json` — `20b3e38c57bdb61777854f9e35be022f68027f48`
- `apps/frontend/package-lock.json` — `f2e7ed9b1c15b2b67ba1a08aad78cae09e78b783`
- `contracts/Cargo.lock` — `d08acefa7cb9b507d48aa226e1e8df0b1cd0bb82`
- `CONTRIBUTING.md` — `2f1d4e586cf3cbd7bce72560ad44b94855262950`

No root `Cargo.lock`, `rust-toolchain`, or `rust-toolchain.toml` exists at this pin.

A source scan of `contracts/Cargo.lock` found no `git+` package source entries at this pin.

## Lockfile / toolchain matrix

| Domain | Installer/toolchain in CI | Authoritative dependency input | Current cache state | Repair direction |
| --- | --- | --- | --- | --- |
| frontend | Node 22 + npm | `apps/frontend/package-lock.json` | none | add npm download cache bound to frontend lockfile |
| backend | Node 22 + pnpm 9.15.9 | root `pnpm-lock.yaml` + workspace topology | setup-node pnpm cache already present | verify/clarify key inputs; avoid redundant cache |
| packages job | Node 22 + pnpm 9.15.9 | root `pnpm-lock.yaml` + workspace topology | setup-node pnpm cache already present | share exact dependency cache semantics with backend |
| contracts | moving Rust `stable` + wasm32 target | `contracts/Cargo.lock` + manifests + resolved Rust toolchain | custom cache includes registry/git + target; key omits lockfile/toolchain | cache downloads only; bind exact lock/toolchain identity |

## Frontend cache contract

The frontend CI intentionally uses npm rather than pnpm, so its cache must follow the npm lockfile, not the monorepo pnpm lock.

A bounded implementation can use `actions/setup-node@v4`:

- `node-version: 22`
- `cache: npm`
- `cache-dependency-path: apps/frontend/package-lock.json`

Important properties:

- setup-node caches npm's **download cache**, not `node_modules`;
- changing `apps/frontend/package-lock.json` changes the dependency key;
- root `pnpm-lock.yaml` changes should not invalidate a frontend-only npm cache unless the frontend package actually depends on that workspace state;
- no `.npmrc`, token, environment file, or auth credential should be added to cached paths.

### Install command note

Current workflow uses `npm install`, not `npm ci`.

Changing to `npm ci` would tighten lockfile reproducibility, but that is a separate behavioral change from “add caching.” Do not silently expand #323 unless maintainers want it.

If the issue's “cache miss produces a clean build” is interpreted as exact lockfile installation, record the current `npm install` semantics and ask whether `npm ci` is intended. A cache repair should not use caching as an excuse for unrelated install-mode churn.

## Backend / shared-package pnpm cache contract

Both backend jobs already use `actions/setup-node@v4` with `cache: pnpm`.

The assigned implementation should not add another `actions/cache` layer over `node_modules` or duplicate pnpm's content-addressed store.

### What still deserves tightening/documentation

- explicitly bind/declare the root `pnpm-lock.yaml` via `cache-dependency-path` if maintainers want the cache input to be visible in workflow review;
- document that both jobs intentionally share the same dependency graph/cache population;
- document the pnpm version (`9.15.9`) and Node version (`22`) as toolchain inputs even if setup-node's internal cache key does not encode every one of those values;
- do not cache generated Prisma client output as a dependency cache;
- do not cache workspace build products.

### `--no-frozen-lockfile` note

Current backend CI runs:

`pnpm install --no-frozen-lockfile`

That means the install is allowed to resolve/update lock state in the job checkout.

#323 does not explicitly ask to change this. The readiness packet treats that as existing policy, not evidence that the cache key should ignore `pnpm-lock.yaml`.

If maintainers require truly immutable dependency reproduction, frozen-lockfile enforcement should be a separately acknowledged scope change.

## Cargo cache contract

The safest repair is to separate **download reuse** from **build-output reuse**.

### Cacheable paths

Keep dependency source/download storage only:

- `~/.cargo/registry`
- `~/.cargo/git`

Do **not** cache:

- `contracts/target`;
- generated WASM output;
- arbitrary `~/.cargo` as a whole;
- `~/.cargo/credentials` or `credentials.toml`;
- environment files or secret-bearing config.

### Exact-key inputs

The exact cache identity should bind at least:

- runner OS / architecture as appropriate;
- `contracts/Cargo.lock`;
- relevant `contracts/**/Cargo.toml`;
- resolved Rust toolchain identity;
- the dependency target if maintainers want target-specific separation.

Because the workflow uses moving `stable` and has no checked-in toolchain pin, a key based only on YAML text cannot identify the actual Rust compiler.

One robust sequence is:

1. run `dtolnay/rust-toolchain@stable`;
2. capture `rustc --version` (or `rustc -Vv`) into a step output;
3. build the cache key from that resolved version plus `hashFiles('contracts/Cargo.lock', 'contracts/**/Cargo.toml')`.

If maintainers instead add a checked-in `rust-toolchain.toml`, then that file becomes an explicit toolchain-key input—but adding it is a toolchain policy change and should not be smuggled into #323 without agreement.

### Restore-key policy

The current broad prefix `cargo-${runner.os}-` can restore downloads from a different dependency generation.

For registry/git source downloads this can be safe because Cargo revalidates required exact versions, but it weakens a simplistic “changed lockfile means physically empty cache” test.

Choose and document one model:

- **strict exact cache:** no broad restore key; lock/toolchain change produces a true empty cache miss; or
- **partial warm restore:** exact key misses, older download cache may restore as a seed, Cargo fetches/validates the new lock; tests must distinguish “exact cache hit” from “safe partial restore.”

Do not claim invalidation solely because a primary key string changed if a fallback silently restored compiled `target` state. Removing `target` closes the highest-risk ambiguity.

## Measurement protocol

The issue asks to measure install time.

The GitHub connector returned no PR-associated workflow runs for the pinned upstream main commit, so this packet does **not** invent historical timing numbers.

An assigned implementation should measure with GitHub's own step timestamps or explicit shell timing.

For each affected domain capture:

- workflow/run URL;
- commit SHA;
- runner OS/architecture;
- exact lockfile SHA;
- exact toolchain versions;
- cache result (`cache-hit` where exposed);
- dependency-install step start/end duration;
- total job duration.

Required comparison:

1. cold/miss run;
2. identical-input warm/hit run;
3. dependency-lock change run;
4. optional revert-to-original-lock run to prove deterministic recovery.

Do not compare unrelated commits or concurrent runner-load conditions as if they isolate cache performance.

## Invalidation experiments

### Frontend npm

1. run with current `apps/frontend/package-lock.json` — MISS;
2. rerun same commit/key — HIT;
3. change one dependency so package-lock changes — exact-key MISS;
4. verify install/build still succeeds;
5. verify reverting lockfile restores original key semantics.

A change only to root `pnpm-lock.yaml` should not invalidate frontend npm cache unless the chosen policy deliberately binds it.

### Backend pnpm

1. record existing setup-node pnpm cache result;
2. rerun same root lock — HIT;
3. change root `pnpm-lock.yaml` — exact-key MISS;
4. verify backend + shared packages install cleanly;
5. verify no `node_modules`, generated Prisma client, dist, or build output is cached.

### Cargo

1. current exact lock/toolchain — MISS;
2. same lock/toolchain — HIT;
3. change `contracts/Cargo.lock` through a real dependency resolution change — exact-key MISS;
4. change resolved Rust toolchain — exact-key MISS if toolchain binding is part of acceptance;
5. prove `contracts/target` starts absent on a cache restore;
6. compile/test/build successfully after a cache miss.

## Path-trigger seam

The workflows are path-filtered.

A #323 implementation will itself modify workflow files, which already trigger their own domain workflow:

- changing `.github/workflows/frontend.yml` triggers frontend CI;
- changing `.github/workflows/backend.yml` triggers backend CI;
- changing `.github/workflows/contracts.yml` triggers contracts CI.

If `.github/workflows/README.md` changes alone, those domain workflows do not necessarily run.

The implementation PR will touch both YAML and README, so this is fine. Do not use a docs-only follow-up as proof of cache behavior.

## Documentation contract for `.github/workflows/README.md`

Add a section that states, per domain:

- cache mechanism/action;
- exact dependency files that drive the key;
- toolchain inputs;
- directories cached;
- directories explicitly **not** cached;
- whether restore prefixes are allowed;
- what a dependency update does;
- local install command unaffected by cache;
- how to inspect `cache-hit` / cache miss in Actions logs;
- how to intentionally force a cold cache for validation without weakening production keys.

The README should clearly preserve the existing policy:

- frontend npm matches Vercel;
- backend pnpm is required for `workspace:*`;
- contracts use Rust + wasm target.

## Credentials / secret safety

Negative cache boundary:

- no `.npmrc`;
- no `.pnpmrc`;
- no `.env*`;
- no GitHub token files;
- no cloud credentials;
- no `~/.cargo/credentials*`;
- no SSH keys;
- no arbitrary home-directory cache path.

Built-in setup-node npm/pnpm caching and scoped Cargo download directories provide a much narrower surface than caching full package-manager homes.

## Proposed bounded workflow changes after assignment

### `frontend.yml`

Add setup-node npm cache using the frontend package lock.

No `node_modules` cache.

### `backend.yml`

Do not create a second cache.

Optionally make `cache-dependency-path: pnpm-lock.yaml` explicit in both jobs and document the existing cache contract.

### `contracts.yml`

Replace current cache behavior:

- drop `contracts/target`;
- add `contracts/Cargo.lock` to the exact key;
- bind the actual Rust toolchain identity;
- preserve only Cargo registry/git downloads;
- choose/document strict exact versus partial warm restore semantics.

### `.github/workflows/README.md`

Document the matrix and invalidation contract.

This is a four-file shape unless maintainers explicitly require helper scripts for timing/key generation.

## Hostile / regression matrix

1. frontend cache key changes when `apps/frontend/package-lock.json` changes;
2. frontend cache key does not accidentally use root pnpm lock as its only dependency input;
3. frontend cache restores npm downloads but not `node_modules`;
4. frontend cache miss still completes install/build;
5. backend still uses exactly one pnpm dependency-cache layer;
6. both backend jobs bind the same root dependency graph intentionally;
7. root `pnpm-lock.yaml` change invalidates exact backend cache;
8. backend cache never contains workspace build output;
9. backend cache never contains Prisma generated output;
10. Cargo exact key changes when `contracts/Cargo.lock` changes;
11. Cargo exact key changes when the accepted toolchain identity changes;
12. Cargo cache paths exclude `contracts/target`;
13. Cargo cache paths exclude credentials;
14. clean cache miss runs fmt/clippy/test/release-wasm successfully;
15. cache restore does not skip or bypass any validation/build step;
16. malformed/missing cache is treated as miss, not job success;
17. cache action failure does not make tests/build disappear;
18. if restore-prefix is retained, exact hit versus fallback restore is distinguishable in evidence;
19. no workflow writes credentials into a cached directory;
20. workflow YAML remains least-privilege and does not introduce write permissions;
21. changing only frontend lock does not poison pnpm/Cargo keys;
22. changing only Cargo lock does not poison npm/pnpm keys;
23. README examples name the actual lockfiles in source;
24. README does not claim `contracts/target` is cached after repair;
25. measurements report commit/lock/toolchain identity and do not compare unrelated runs.

## Review hazards

### Hazard 1 — “add pnpm cache” duplicates existing behavior

This is the highest-likelihood low-value patch.

Backend already has `cache: pnpm` in both jobs.

A PR that merely adds another `actions/cache` for pnpm store risks double caching and slower restore/save while pretending to close the issue.

### Hazard 2 — cache key hashes Cargo manifests but not Cargo.lock

Current contracts workflow already has this defect.

The lockfile is the resolved dependency graph and must be a primary invalidation input.

### Hazard 3 — caching `target` contradicts the issue premise

The issue explicitly wants dependency caching without unsafe build-output caching.

Keep compiled artifacts out of the dependency cache.

### Hazard 4 — “stable” is a moving toolchain

Hashing workflow YAML does not identify the resolved stable compiler.

Either capture the actual version or adopt a checked-in toolchain pin as an explicitly approved policy change.

### Hazard 5 — fake cache-hit proof

Two runs of different commits, different runners, or different dependency graphs do not prove a hit.

Evidence must bind exact key inputs and the action's cache result.

### Hazard 6 — credentials leak through overbroad home-directory caches

Cache exact package-manager download directories only.

## Definition of readiness

The lane is ready for an assigned worker when they can answer, before editing:

- What exact lockfile feeds each domain?
- Which caches already exist?
- Which exact directories are safe to reuse?
- What current cache is unsafe/stale?
- What identifies the actual Rust toolchain?
- How is exact cache hit distinguished from fallback restore?
- How will miss/hit/invalidation timing be recorded?
- Which files change?
- How is a clean miss proven?
- How are credentials and build outputs excluded?

This packet answers those questions for the pinned source.

## Assignment gate

Before implementation:

1. refresh GrantFox assignment;
2. refresh GitHub issue assignee/comments;
3. search active PRs for #323/cache work;
4. refresh upstream `main`;
5. rehash the four workflow/docs files and lockfiles above;
6. resolve any source drift before carrying this plan forward;
7. only then create the upstream branch/PR required by the issue.

No upstream PR should be opened while the issue remains unassigned.
