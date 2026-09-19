# GrantFox baseline — Gryd-lock/grydlock-testkit #69 · downstream compatibility R2

**Operation:** `GFOX-FRESH-GRYD-69/R2-downstream-generated-module+consumer-pin-audit`  
**Worker:** ZZ-Solstice-69 · GPT-5.6 Sol  
**Observed:** 2026-09-19  
**Disposition:** LIVE_GAP_WITH_PARTIAL_AUTOMATION / implementation remains provider-assignment-gated

## Pinned source state

| Surface | Pinned revision | Evidence |
|---|---|---|
| `Gryd-lock/grydlock-testkit` | `7064404d6e7c44df1f980d532d23901651d79426` | current default-branch head observed |
| testkit `destinations.json` | blob `c8f918089f700982d7347cc1d8af8d8677fd5774` | includes `risk_pattern` + required `fixture_status: "synthetic-only"` |
| testkit `scores.json` | blob `f27d17c781a9059cc1304af93def554391b63876` | id -> numeric score map |
| testkit consumer workflow | blob `05968b778c3ad91e096e4e954c79fa9178662092` | pins adapter `7e5b1b09860ce713abe507a59a3b7a226e09ebfb` |
| `Gryd-lock/grydlock-oracle-adapter` | `81b09e371694998d5c42e66be656aaaef409c176` | current observed main / release 1.3.0 commit |
| adapter sync implementation | commit `480691fcc880806357a09543fc8b8f5ba1926b3b` | automated weekly/manual testkit sync |
| adapter vendored destinations | blob `b37f01d6e90f63b3bdb7c5c1475c27785a1b05be` | does **not** include current testkit `risk_pattern` / `fixture_status` |
| adapter vendored scores | blob `f27d17c781a9059cc1304af93def554391b63876` | matches current testkit score blob |
| adapter schema | blob `24ff153ac138e3836b886942677e41c6e2ab83a8` | recognizes only `id,type,label,notes` for destination runtime objects |
| adapter generated-module sync test | blob `aff41d77d73dad6b287a18e50e974e1c3b8e615b` | generated TS must equal **local vendored JSON** |
| adapter cross-repo sync script | blob `d2ef84bb24ede952507acf5e1cb413ec1d8d0ec3` | checks tiers + RiskOracle, not fixture schema |
| `Gryd-lock/grydlock-extension` | `4c3cc11c37288732b4d27d73ae473815f4fab40a` | indirect consumer through adapter |
| `Gryd-lock/grydlock-research` | `24bab091f5ec9dc98af1a69bacaeb547b15d980b` | evaluation/design consumer declared by project docs |

GitHub issue #69 is OPEN and unassigned at observation. The connected upstream repository is pull-only for this account. No reward, award, payment, or provider assignment is asserted by this packet.

## Current-source correction: “manual copies” is partially stale

The issue correctly identifies a cross-repository compatibility risk, but its “adapter manually copies JSON/generated modules” premise is no longer complete.

Adapter commit `480691f...` added:

- a weekly/manual `Sync Testkit Fixtures` workflow;
- `npm run sync:fixtures`;
- a script that fetches `destinations.json` and `scores.json`;
- local schema validation;
- regeneration of `*.text.ts` companion modules;
- tests before creating an automated update PR.

That automation is useful, but it does **not** satisfy #69's release-compatibility contract.

## Verified live gaps

### 1. Producer identity is a moving branch, not a release identity

`scripts/sync-testkit-fixtures.mjs` uses:

`https://raw.githubusercontent.com/Gryd-lock/grydlock-testkit/main`

The resulting adapter PR has no machine-readable producer version/commit contract. Two runs with identical adapter source can ingest different fixture bytes. A tagged adapter release therefore cannot answer “which exact testkit dataset/schema was this built against?” from a compatibility manifest.

### 2. The current adapter mirror is already stale in a meaningful way

Current testkit `destinations.json@c8f918...` requires and publishes `risk_pattern` and `fixture_status`. Current adapter vendored `destinations.json@b37f01...` lacks both fields.

The score file happens to match exactly (`f27d17...` on both sides), so a score-only smoke check can look healthy while destination metadata has drifted.

This is exactly the class of partial compatibility failure #69 should make explicit.

### 3. Generated-module verification is local, not producer-bound

`tests/fixtureText.sync.test.ts` is a good stale-generation guard: it regenerates the expected `*.text.ts` source from the adapter's **local** vendored JSON and fails if the committed generated module differs.

But that proves:

`generated module == local vendored JSON`

It does **not** prove:

`local vendored JSON == declared testkit release artifacts`

A stale vendor copy plus faithfully regenerated `*.text.ts` can therefore be green.

### 4. Adapter runtime parsing deliberately drops unknown destination metadata

Adapter `DestinationFixture` contains only `id`, `type`, `label`, and `notes`. The incremental parser stores only those fields and calls `skipValue()` for all other keys.

That makes current additive fields such as `risk_pattern` / `fixture_status` non-breaking for the adapter's existing score lookup path, but they are not preserved in the runtime object. Compatibility must therefore be directional and capability-aware: “loads without throwing” is weaker than “consumer preserves/understands the producer contract.”

### 5. The producer-side consumer check is pinned to an older adapter

Testkit's `.github/workflows/consumer-contract-test.yml` pins:

`ORACLE_ADAPTER_REF=7e5b1b09860ce713abe507a59a3b7a226e09ebfb`

and runs weekly/manual only.

Pinning is correct in principle, but there is no machine-readable declaration connecting that consumer SHA to supported testkit schema/version ranges, and the pin can lag the actual released consumer. The README itself calls this limitation out.

### 6. Existing cross-repo sync covers other contracts, not fixture compatibility

Adapter `scripts/check-cross-repo-sync.mjs` verifies warning tiers and `RiskOracle.getScore`. It does not verify fixture producer identity, destination schema version, generated text modules, supported consumer revisions, or migration policy.

## Directional compatibility model

Compatibility should be represented as:

`consumer revision C accepts producer schema S and dataset release D under capabilities K`

not as a single global “compatible / incompatible” flag.

Recommended machine-readable release artifact (name illustrative):

`fixture-compat.json`

Minimum fields:

- manifest schema/version;
- testkit dataset version;
- immutable testkit source commit/tag;
- logical schema versions for `destinations`, `scores`, transaction index, scenarios as applicable;
- artifact path + cryptographic digest;
- change classification: additive / behavioral / breaking;
- supported consumer entries containing repo + exact tested revision (or an explicit semver range only after consumers themselves publish stable versions);
- capabilities each consumer is verified to preserve (for example `score_lookup`, `destination_base_fields`, `risk_pattern_metadata`, `fixture_status_metadata`);
- migration document/reference for every unsupported/breaking edge.

Do not infer support from “latest main.”

## Compatibility rules

1. **Artifact byte identity is immutable.** A release manifest binds exact bytes; scheduled “latest” synchronization may open a proposal, but a release gate must verify an immutable commit/tag and digest.
2. **Unknown optional fields are additive only for tolerant consumers.** Current adapter parser is tolerant but drops them, so it can claim base-field compatibility, not metadata-preservation compatibility.
3. **Adding a newly required field is directional.** New consumers may reject old producer releases even if old consumers accept new producer files.
4. **Removal/rename/type changes are breaking** for any consumer that reads the field.
5. **Semantic changes can be breaking without JSON-shape changes.** Label vocabulary, score meaning/range, identifier semantics, network/passphrase semantics, or transaction-envelope interpretation require an explicit capability/version decision.
6. **Generated artifacts are derived outputs.** Their provenance is the exact source artifact digest + generator revision. “Generated file matches local JSON” is necessary but insufficient.
7. **Unsupported versions fail before consumption** with a stable incompatibility code plus migration guidance; no silent fallback to an arbitrary older local copy.
8. **At least one downstream revision is a release gate.** Weekly monitoring is additional defense, not the only compatibility proof.

## Concrete downstream gate for the current adapter

For each candidate testkit release:

1. resolve testkit to an immutable release commit and verify manifest artifact digests;
2. checkout the adapter at the **declared supported revision**;
3. replace its vendored `destinations.json` / `scores.json` from the release manifest, not moving `main`;
4. run its generator;
5. assert generated `destinations.text.ts` / `scores.text.ts` exactly equal generator output;
6. run adapter fixture-schema tests + `StubOracle` tests;
7. run a capability test that explicitly states whether new fields are preserved, ignored, or unsupported;
8. record tested producer+consumer SHAs in the release evidence.

A scheduled adapter updater can still fetch “latest” for discovery, but the resulting PR must freeze the resolved testkit commit and persist it in the compatibility metadata before merge.

## Hostile regression matrix

| Mutation | Expected disposition |
|---|---|
| Add optional `risk_pattern` to a destination | PASS only for consumers declaring unknown-field tolerance; capability report says whether field is preserved |
| Add required `fixture_status` while testing an older producer against a newer consumer that requires it | FAIL `UNSUPPORTED_PRODUCER_SCHEMA` with migration guidance |
| Remove or rename `notes` | FAIL before release for current adapter base-field contract |
| Change a score to string or outside 0..100 | FAIL schema validation |
| Add destination without matching score | FAIL producer validation / consumer contract |
| Change vendored JSON without regenerating `*.text.ts` | FAIL generated-module drift test |
| Keep stale vendored JSON + matching stale generated module while manifest points at newer producer | FAIL provenance/digest check even though local sync test is green |
| Declare unsupported schema major | FAIL stable incompatibility code + supported range + migration reference |
| Fetch same branch URL twice after branch advances | release evidence remains deterministic because manifest resolves/fixes immutable commit/digests |

## Suggested implementation split after assignment

**Testkit / producer**
- introduce a first-class compatibility manifest and schema-version declarations;
- make release notes/manifests identify exact supported consumer evidence;
- extend consumer-contract workflow to read the manifest and exercise declared pinned consumer revisions;
- make unsupported-version tests and migration guidance first-class.

**Oracle adapter / direct consumer**
- persist upstream testkit commit/tag + artifact digests when syncing;
- stop treating moving `main` as release provenance;
- make fixture capabilities explicit (base fields vs metadata preservation);
- keep existing generated-module local-drift test, but add producer-bound artifact verification.

**Extension / research**
- consume the adapter's declared fixture capability/version rather than assuming fixture semantics indirectly;
- add direct fixture compatibility checks only if they begin importing fixture artifacts themselves.

## Assignment-time acceptance proof

A complete assigned implementation should return:

- immutable producer release/commit;
- machine-readable compatibility manifest;
- exact downstream revision(s) exercised;
- generated-module provenance proof;
- additive-field PASS evidence;
- removed-field FAIL evidence;
- stale-generated-module FAIL evidence;
- unsupported-version FAIL evidence including migration guidance;
- existing project CI results;
- no live-oracle dependency for this compatibility path.

## Authority / custody fence

This is a source-pinned R2 compatibility audit and implementation contract. It changes no upstream source, GrantFox assignment, issue assignee, reward, wallet, award, or payment state. Assignment-dependent upstream implementation should start only after current provider/maintainer authority is verified.
