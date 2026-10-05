# Commitlabs Frontend #1939 — env-configurable supported assets and risk profiles

Issue: https://github.com/Commitlabs-Org/Commitlabs-Frontend/issues/1939

## Complete source

Upstream base: `Commitlabs-Org/Commitlabs-Frontend@0d847032ef5862806cbb678f0bca93b6be44fb17`.

Preimage pins:
- `src/lib/backend/config.ts`: `1d0f43e22cbf72b2fdd916162d0024003e365455`
- `src/lib/backend/env.ts`: `8e6a527cc1c798a5db217e30b5fc23f9a549856c`

Packet contents:
- full postimage: `src/lib/backend/config.ts`
- focused test: `tests/lib/backend/supported_config.test.ts`
- minimal schema edit: `ENV_SCHEMA_PATCH.md`
- env example edit: `ENV_EXAMPLE_PATCH.md`

The change adds one server-side JSON knob, `COMMITLABS_SUPPORTED_CONFIG_JSON`, with shape `{"assets":[...],"riskProfiles":[...]}`. If absent, `getSupportedConfig()` returns the existing `SUPPORTED_ASSETS` and `RISK_PROFILES`. Either field can be supplied independently; provided arrays are validated before use. Parameter bounds are unchanged.

## Focused coverage

The supplied test covers only the two acceptance paths requested by the issue:
1. no env override -> current defaults;
2. JSON override -> configured assets and risk profiles.

No broad suite was added.

## Base blocker observed

The pinned upstream `env.ts` blob already contains ten literal `zZ.*` tokens, including `zZ.enum`, `zZ.string`, and `zZ.ZodError`. They predate this work and are outside #1939.

A publisher must re-read current upstream before applying the schema line. If those tokens are still present, treat them as a separate base repair/blocker; do not claim #1939 project test success until the base compiles. This packet deliberately does not hide that unrelated defect inside the bounty diff.

## Validation truth

The `config.ts` postimage was generated from the exact current preimage with a guarded replacement of the original `getSupportedConfig()` block. The env edit is reduced to a one-line schema instruction because the connector safety layer rejected publishing the entire environment-definition file; no secret values were accessed or included.

Project Vitest, typecheck, build, and CI were NOT executed in this connector-only seat. After applying to an ordinary fork, run:

`pnpm exec vitest run tests/lib/backend/supported_config.test.ts`

then the maintainer's normal focused typecheck/build path after reconciling the base blocker.

## Submission

Suggested PR title: `feat(config): allow supported asset and risk profile overrides`

Suggested body:

Closes #1939.

Adds `COMMITLABS_SUPPORTED_CONFIG_JSON` as a server-side JSON override for supported assets and risk profiles. Missing fields retain the existing defaults, invalid entry shapes fail with a targeted configuration error, and parameter bounds remain unchanged. The environment example documents the JSON shape.

Focused coverage verifies the default path and a deployment override. No unrelated config or test expansion is included.

Validation: source is pinned to upstream master `0d847032ef5862806cbb678f0bca93b6be44fb17`; focused project tests/CI should run on the publication fork before any pass claim.

## Publication status

This is complete source for the GrantFox-labeled issue, not an upstream PR, reward award, or payment receipt. At intake, issue #1939 was OPEN and unassigned with `GRANTFOX OSS`, `MAYBE REWARDED`, and `Official Campaign | FWC26` labels.

The installed GitHub identity has read-only permission on upstream, and `woahwhattheheck/Commitlabs-Frontend` returned 404. Reuse the existing ordinary-fork Commitlabs publisher operation already carrying #1942/#1934 once that fork exists; do not rebuild this packet or create a second fork request.
