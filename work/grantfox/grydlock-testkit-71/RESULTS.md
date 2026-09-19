# GrantFox spike — Gryd-lock/grydlock-testkit #71 · publication prototypes

**Operation:** `GFOX-FRESH-GRYD-71/R2-two-prototypes+rollback-integrity-measurement`  
**Worker:** ZZ-Solstice-69 · GPT-5.6 Sol  
**Observed:** 2026-09-19  
**Pinned producer:** `Gryd-lock/grydlock-testkit@7064404d6e7c44df1f980d532d23901651d79426`  
**Related source packets:** #69 compatibility audit; #66 immutable-archive lane remains the production archive owner.

## Current topology

Current testkit source already points in three different directions:

- `package.json` is version `0.1.0` but `"private": true`;
- the README tells consumers to pin a tagged release / clone `v0.1.0`;
- the oracle adapter has since gained an automated fixture-sync job, but it fetches moving `testkit/main`, vendors JSON, and regenerates browser-loadable `*.text.ts` modules;
- #69 source audit proved the current adapter's vendored destination metadata can lag current testkit even when locally generated modules are internally consistent.

So the publication decision is not merely “Git vs npm.” It must separate **canonical immutable data identity** from **consumer ergonomics / browser representation**.

## Prototype method

`PROTOTYPES.mjs` is intentionally miniature and offline. It does not pretend to be #66's production archive generator. It uses two synthetic fixture rows while binding the experiment metadata to the real current testkit commit.

Run:

```bash
node work/grantfox/grydlock-testkit-71/PROTOTYPES.mjs
```

Execution environment used for the recorded receipt:

- Node `v22.16.0`
- npm `10.9.2`
- no network access

### Prototype A — content-addressed release bundle

The harness creates a tiny `data/` directory plus a manifest that binds:

- manifest schema;
- dataset version;
- immutable upstream source commit;
- per-file byte count;
- per-file SHA-256.

It then proves:

1. clean verification passes;
2. a changed score with an unchanged manifest fails closed as `ARTIFACT_INTEGRITY_MISMATCH`;
3. asking for an unsupported dataset version fails closed with migration guidance;
4. the preserved original directory can be selected again and independently re-verified as a rollback.

This is a **format prototype**, not the production deterministic archive implementation owned by #66.

### Prototype B — installable data package

The harness creates a local package `@gryd-lock/testkit-data-prototype@0.1.0` containing:

- raw JSON;
- generated ESM modules for browser/bundler-friendly imports;
- the same source-bound manifest;
- package export mappings.

It then runs `npm pack --json --ignore-scripts`, installs the exact resulting tarball into a clean local consumer, imports the installed generated modules, and re-verifies the installed manifest.

That demonstrates both install ergonomics and two integrity layers:

- npm tarball integrity / shasum for transport identity;
- fixture manifest hashes for internal data identity.

## Measured receipt

| Check | Result |
|---|---|
| release-bundle clean verify | PASS |
| release-bundle tamper detection | PASS — `ARTIFACT_INTEGRITY_MISMATCH:data/scores.json` |
| unsupported-version guidance | PASS — `UNSUPPORTED_DATASET_VERSION:0.1.0:expected=9.9.9:migrate=select-compatible-release` |
| archived release rollback/reverify | PASS |
| `npm pack` | PASS |
| exact local tarball install | PASS |
| consumer ESM import | PASS |
| installed package manifest verify | PASS |
| npm tarball | 1,041 bytes |
| npm-reported unpacked package | 2,201 bytes |
| release prototype directory | 996 bytes |
| npm tarball integrity | `sha512-CECjcIzo37CPfh8iihBfyB1l5rNYmq3WKuVhSTcwt2jzyrRQUUnZr/WxoLh4pJwLUy1ju/77gvBJOu/bCq4sYg==` |
| npm tarball shasum | `ddf963a182b9abf72e02d90fcb6c3c2deae05769` |

**Do not extrapolate the byte ratio to the real corpus.** This is a deliberately tiny two-row prototype, so fixed package metadata and duplicated generated modules dominate. The useful measured result is qualitative: package-shaped distribution adds representation/publication overhead but gives a much cleaner consumer install/import path.

## Comparison matrix

| Approach | Reproducibility | Integrity | Consumer ergonomics | Browser/bundle behavior | Rollback | Maintenance / risk |
|---|---|---|---|---|---|---|
| Git tag + clone/checkout | High only when consumer pins immutable commit SHA; tag name alone is mutable unless protected | Git object identity | Low/medium; requires Git + copy/generate step | Consumer still needs JSON handling/generated module step | Easy checkout of prior commit/tag | Low publishing machinery, high downstream vendoring/sync burden |
| GitHub Release archive + manifest | High when release points to immutable source and manifest binds every artifact | **High** with checksums/content identity | Medium; download/extract/verify is explicit | Can ship only raw data or selected projections | **Excellent**: retain prior immutable archive | Good canonical format; #66 should own production generator/verifier |
| npm package exporting JSON + generated ESM | High with exact version + registry integrity | High transport integrity plus internal manifest if retained | **Best** install/import UX | Strongest direct bundler ergonomics; generated modules can duplicate raw bytes | Exact prior version install is easy | Requires registry publication policy, package lifecycle, export compatibility, and browser bundle discipline |
| Standalone package-shaped data artifact attached to GitHub Release | **High** | **High** manifest + release asset identity | High if tarball layout/exports are stable; no registry needed | Can contain raw data + generated projections | **Excellent** by release/version | Moderate; combines #66 canonical archive with package-like layout without committing to npm |
| Generated modules only | Medium unless generator + raw source digest are also bound | Weak as sole source; generated bytes can be internally consistent but stale vs producer | High for browser import | Good runtime UX | Possible, but provenance is awkward | Reject as canonical source because it duplicates/obscures raw-data provenance |

## Recommendation

Use a **two-layer model**:

1. **Canonical publication:** immutable release asset + machine-readable manifest from #66, binding dataset version, source commit, complete file list and cryptographic digests. This is the source of truth for reproducibility, rollback, and compatibility evidence.
2. **Consumer projection:** package-shaped data layout generated *from that exact canonical manifest*. Initially this can be another GitHub Release asset; npm publication is optional later, not required to gain a stable package structure and export map.

If npm is adopted, publish only a deterministic projection whose manifest points back to the same canonical release identity. Never allow npm bytes and release-archive bytes to become independent sources of truth.

This gives the adapter/research consumers package ergonomics without making a registry the sole custody layer, and it lets browser-friendly generated modules remain derived artifacts whose provenance is explicit.

## Why not the alternatives?

**Git tags alone:** useful source pin, but they leave every consumer responsible for selecting/copying/generating files. Current adapter drift demonstrates that this is not enough operationally.

**npm as the only canonical channel:** excellent ergonomics, but the repo is currently private as an npm package and no registry publication contract exists. Making the spike depend on a new registry lifecycle is unnecessary when #66 already motivates immutable release assets.

**Generated modules as the sole artifact:** #69 already shows the failure mode: generated modules can perfectly match a stale local vendor copy. They must be a projection of a manifest-bound raw source, not the canonical identity.

**Raw GitHub `main` URLs:** reject for release consumption. They are convenient discovery/update inputs, not reproducible release identity.

## Migration plan

### Phase 0 — current state, no consumer break
- #66 defines the production deterministic release archive + manifest.
- #69 defines directional producer/consumer compatibility and required provenance.
- Keep existing adapter vendoring path operational while introducing manifest metadata.

### Phase 1 — canonical release identity
- publish immutable testkit release artifacts with source commit + per-file hashes;
- make consumer-contract CI consume a selected immutable release/manifest rather than moving `main`;
- record exact downstream revision tested against each producer release.

### Phase 2 — package-shaped projection
- add a deterministic package layout generated from the canonical release;
- export raw data and browser-friendly generated modules;
- include the canonical manifest unchanged or cryptographically linked;
- test local tarball install/import in CI before any registry decision.

### Phase 3 — optional registry
- if maintainers want npm ergonomics, publish the same deterministic projection under a stable package name;
- pin exact versions in consumers and retain manifest verification;
- preserve GitHub Release assets as canonical archival/rollback custody.

### Phase 4 — retire ad-hoc moving-main vendoring
- adapter updater may still discover newer releases automatically, but it should resolve to immutable release identity before opening/merging a sync PR;
- refuse unsupported schema versions with migration guidance rather than silently loading an arbitrary local copy.

## Assignment-time acceptance

An assigned upstream spike can reuse this harness but should run prototypes against the **real complete release file set**, not the miniature fixture:

- at least three delivery models compared;
- at least two runnable prototypes;
- current adapter/research consumer install path demonstrated;
- manifest/tarball integrity verified;
- rollback to a prior exact version demonstrated;
- browser bundle impact measured on the real corpus;
- rejected alternatives tied to current source constraints;
- final recommendation aligned with #66 archive and #69 compatibility contracts.

## Authority fence

This is an offline research prototype and source-readiness packet. It does not implement #66 production archive tooling, mutate upstream source, publish npm, create a GitHub Release, apply for GrantFox assignment, or assert reward/payment authority.
