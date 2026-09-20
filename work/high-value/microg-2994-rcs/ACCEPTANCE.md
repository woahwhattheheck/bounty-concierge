# microG/GmsCore #2994 — RCS acceptance and hardware handoff

Snapshot: 2026-09-20 UTC

This packet exists to **reduce duplicate implementation work** on the $14,999 RCS bounty, not to create another competing implementation or claim payout ownership.

## Source-backed economics

The canonical issue is `microg/GmsCore#2994`, open and unassigned at this snapshot. BountyHub bot receipts in the issue thread record:

- $5,000 initial bounty (comment `3145368949`)
- +$5,000 contribution, total $10,000 (comment `3156926692`)
- +$4,999 contribution, total **$14,999** (comment `3550980382`)

The BountyHub founder also stated in-thread that the original funding was paid in advance. This packet does **not** infer who is entitled to any portion of the bounty.

## Maintainer routing is decisive

The current implementation lane is no longer greenfield.

On 2026-03-02, microG maintainer `mar-v-in` required any RCS implementation claim to include a physical phone with a SIM in the test environment. On 2026-06-07, the maintainer explicitly asked contributors to stop creating new implementations and instead test/review the existing RCS PRs, starting with the oldest work.

The maintainer separately warned that merge suitability and bounty attribution are different questions because many contributors shared findings and code. Therefore:

**Do not open another RCS implementation PR. Do not represent a merge as automatic bounty ownership.**

## Existing carriers

### Working reference: upstream PR #3360

`microg/GmsCore#3360` (opstic) is the oldest working Asterism line referenced by the maintainer.

Pinned head: `2a680646cbfaf3a4a484bd4dc8f53c21404c8c4b`

Bounty sponsor `unpluggederan` reported on 2026-05-06 that this line worked in testing with 30 users across 10+ carriers, including MT/SMS and TS.43 provisioning, successful activation/operation in Google Messages, and persistent connections. The same sponsor explicitly separated that technical validation from bounty attribution.

Caveat: #3360's recorded base is older than current upstream master, so it is a **working reference**, not proof that its present head can be merged into current master without rebase/review.

### Current-master integration candidate: upstream PR #3808

`microg/GmsCore#3808`

- head: `223f91fe0cde7be17bcd19bb0e28087526d98bc4`
- base: current upstream master `4c74e5acb79479004428be755547432294639878`
- 78 commits, 124 changed files
- GitHub currently reports mergeable=true / mergeable_state=unstable
- zero submitted GitHub reviews at snapshot

#3808 consolidates earlier RCS work and adds substantial hardening, but its own thread documents a still-open source integration gap.

## Concrete source blocker: IMS Phenotype provider delivery

Open follow-up: `Chess-Debug/GmsCore#4`

- base: exact #3808 head `223f91fe0cde7be17bcd19bb0e28087526d98bc4`
- head: `7de6271183220679b983c208148061bcf20f8870`
- 3 files, +43/-1
- state: OPEN

#3808 already configures:

`com.google.android.ims.library -> RcsProvisioning__min_gmscore_version_for_upi_without_acs_fallback_met=true`

but its `ConfigurationProvider` returns an empty cursor for that namespace. The follow-up exposes the already-configured value through the provider path.

Its locked Pixel 3 A/B is useful and narrow:

- before: bounded provider query returns no result
- after: the same query returns the configured RCS flag as true

That proves provider delivery, **not** full RCS activation. Because this follow-up is still open and not part of #3808's exact head, the verifier intentionally classifies #3808 as `HOLD_SOURCE_INTEGRATION_AND_HARDWARE`.

## Remaining physical acceptance

After the provider-path blocker is integrated or otherwise superseded, a hardware-capable seat should test the **existing carrier**, not write a new implementation.

Required gates:

1. **Locked/no-root Google Messages E2E** — on a microG-only physical phone, activate RCS and prove send + receive.
2. **Real-carrier TS.43** — exercise provisioning on a real carrier/SIM rather than an emulator-only or mocked path.
3. **Samsung / Play Integrity compatibility** — physically exercise the Samsung CompositeToken / integrity path on relevant hardware.

Record device model, bootloader/root state, SIM/carrier, Google Messages version, exact microG commit, build variant, provisioning flow, activation result, send/receive result, and sanitized failure stage if any. Do not publish phone numbers, tokens, authentication material, raw SMS challenges, or unrelated device identifiers.

## Deterministic gate

Run:

```bash
python work/high-value/microg-2994-rcs/verify_acceptance.py
```

Current expected result is exit 1 with:

- `HOLD_SOURCE_INTEGRATION_AND_HARDWARE`
- one source blocker for `Chess-Debug/GmsCore#4`
- three missing hardware gates
- `NEW IMPLEMENTATION PR: NO`
- `PAYOUT CLAIM: NO`

The gate becomes `READY_FOR_MAINTAINER_REVIEW` only when all required source follow-ups are marked merged/superseded by equivalent reviewed source and every required physical gate has explicit pass evidence. It never auto-claims the bounty.
