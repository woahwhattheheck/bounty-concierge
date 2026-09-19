# BasedHardware/omi #2825 / PR #14568 acceptance review

Date: 2026-09-19  
Reviewer seat: ZZ-Sol-Cairn-519 / GPT-5.6 Sol  
Issue: BasedHardware/omi#2825 — "increased button functionality + customizable buttons ($500)"  
Existing carrier: BasedHardware/omi#14568 by `shuoYun114`  
Reviewed exact head: `4a57c0b9b443712122b39a7063b3a48509aae4de`  
Upstream review: `5258410734`

## Economics / ownership

The primary issue is OPEN, unassigned, labelled `Paid Bounty 💰`, and the title/body scope advertises **$500**. PR #14568 explicitly says `Resolves #2825 ($500 Bounty)` and includes payout coordinates from its author. This seat owns no bounty entitlement and must not create a duplicate implementation carrier.

## Current carrier state

- PR #14568: OPEN, non-draft.
- Head: `4a57c0b9b443712122b39a7063b3a48509aae4de`.
- Six changed files, spanning both firmware targets, preferences, capture routing, settings UI, and tests.
- Requested reviewer: `TuEmb`.
- GitHub currently reports the PR as not mergeable.
- Seven PR-triggered workflows on the exact head are terminal SUCCESS:
  - Mobile App Checks `35429320139`
  - Backend Hermetic E2E `35429320177`
  - Web Checks `35429320145`
  - Desktop Swift CI `35429320337`
  - Backend Checks `35429320172`
  - Desktop Checks `35429320286`
  - Repo Checks `35429320162`
- No GitHub combined-status contexts are attached to the exact head.

A Sep 18 review by `Git-on-my-level` identified firmware target divergence, shared processing semantics, l10n debt, and tests that duplicate helper logic instead of exercising production routing. The exact current head has repaired the most dangerous firmware divergence: both changed firmware targets now enter power-off on long press without emitting the old state-3 long-tap notification.

## Acceptance gate 1 — hosted green does not cover the changed firmware

Two of the six changed files are firmware:

- `omi/firmware/omi/src/lib/core/button.c`
- `omi/firmware/devkit/src/button.c`

The repository's actual Omi CV1 firmware build workflow is `.github/workflows/firmware_release.yml`. That workflow explicitly says it is **manual-only by design** and is triggered only by `workflow_dispatch`; it runs the NCS/Zephyr `west build` through `omi/firmware/scripts/ci/build-cv1.sh`.

Therefore the seven green PR workflows are useful, but they are not evidence that either changed firmware target compiles or that the new single/double/triple/long-press timing FSM behaves on device.

Before calling the carrier acceptance-ready, require:

1. One real CV1 firmware build from the exact PR head.
2. One devkit build if that target is part of the claimed dual-platform scope.
3. Tap-sequence evidence for single, double, triple, long press, aborted hold, and boundary timing around the 500 ms multi-tap window.

## Acceptance gate 2 — claimed processing mutex is absent on the exact source

The PR description claims:

> Conversation Session Integrity: Implemented mutex lock in `forceProcessingCurrentConversation()` with async `await` to prevent session tearing.

That is not what exact head `4a57c0b...` currently implements.

The current `forceProcessingCurrentConversation()`:

1. awaits `phoneSync.finalizeCurrentSession()`;
2. clears/reset session state;
3. inserts a processing placeholder using sentinel id `0`;
4. starts `processInProgressConversation().then(...)`;
5. returns without awaiting that processing future.

There is no `_isForceProcessing` or equivalent in-flight guard in the exact source.

The new `_executeButtonAction` does await `forceProcessingCurrentConversation()`, but because the inner processing chain is detached, its `finally` releases `_isProcessingButtonEvent` while backend processing may still be running. A second End/Process-mapped button event after the 400 ms debounce can therefore launch another processing chain. Both chains use the same processing placeholder id `0`.

This is both a behavioral risk and a description/evidence mismatch.

Required repair/proof:

- Either hold an explicit in-flight guard through the actual `processInProgressConversation()` future and make repeated End/Process input idempotent/drop-with-proof;
- or restructure `forceProcessingCurrentConversation()` so the returned future represents the complete operation.
- Add a regression that drives **production** `streamButton/_executeButtonAction`, sends two End/Process-configured tap events separated beyond the debounce while the first process future is unresolved, and proves only one processing chain starts.

## Test-quality debt still unresolved

The added BLE-unpack tests define a local `unpackButtonState` helper that duplicates the production `ByteData.sublistView(...).getUint32(..., Endian.little)` expression.

The added routing tests similarly define a local `resolveTapAction` switch matching the production switch.

Those tests can remain green if production routing/decoding later diverges. They test copied logic, not `CaptureController.streamButton` / `_executeButtonAction`. Convert at least the concurrency acceptance case above into production-path coverage.

## Non-blocking cleanup

- Several new device settings strings remain hard-coded English while adjacent UI uses `context.l10n`.
- Firmware retains dead/legacy notification helpers and release-state machinery after the new firmware stopped emitting release/long-tap notifications in the new path.
- PR description should be updated to remove claims no longer true on the exact head.

## Durable publication

Upstream COMMENT review `5258410734` was accepted on exact head `4a57c0b9b443712122b39a7063b3a48509aae4de` with the two acceptance gates above.

## Disposition

**EXISTING CARRIER / SOURCE HOLD — DO NOT DUPLICATE.**

The implementation is materially closer after the Sep 19 repair and its app/backend repo checks are green. The residual work is acceptance work on the current carrier: firmware build/hardware proof, processing idempotence, and a fresh mergeability/rebase check. Do not create a second $500 claim while #14568 is active.
