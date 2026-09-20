# microG GmsCore #580 — connectionless Cast gap map

Status: source/review packet only; no upstream claim or duplicate implementation.

Pinned upstream: `microg/GmsCore@4c74e5acb79479004428be755547432294639878` (`master`, observed 2026-09-19 EDT).
Canonical issue: https://github.com/microg/GmsCore/issues/580 — OPEN, unassigned at readback.

## Economics fence

The current issue thread carries two distinct BountyHub offers, not one guaranteed award:
- $250 offer: `27c3cfe0-da9e-4192-848a-b676402de81c`
- $150 offer: `ef91cb1e-dd69-4a33-bd90-21c9679c9247` ($100 initial + later $50 contribution)

Treat the visible $400 as posted competitive supply only. BountyHub acceptance still depends on a resolving PR and the relevant bounty creator accepting the claim. A historical direct $500 offer quoted in the issue expired at end-2025 and is not counted here.

## What current master still lacks

The pinned master is still the classic Cast implementation:

- `ICastDeviceController.aidl` blob `4f91cdda20c2fc8d61b7e293ae0da6e48dfde663` has only the classic surface (`disconnect=0`, `stopApplication=4`, `sendMessage=8`, namespaces 10/11, launch 12, join 13). It has no connectionless `connect`, listener setter, or unregister call.
- `ICastDeviceControllerListener.aidl` blob `1d26c14b03fa8c9d43a3039ab9a3b101cb95b733` ends at `onDeviceStatusChanged=12`; no connectionless readiness callback is present.
- `CastDeviceControllerService.java` blob `d494a012afd4bb66bbdfa5378342889c1f4128ed` registers only `GmsService.CAST` and returns ordinary `onPostInitComplete`; it does not admit `CAST_API(161)` or advertise `cxless_connect` / `cxless_set_listener` features.
- `CastDeviceControllerImpl.java` blob `e93e3c139069c79e1f80eb630e49db760bb07326` gets the listener only from bind extras, never implements the cxless connect/listener handshake, and still reports send success from inbound messages.
- `CastContextImpl.java` blob `be1c5935c0ac3d2f0a178261d00261dff56d5c7f` does an exact-only session-provider lookup and does not register `MediaRouterCallbackImpl`, so framework route selection is not wired into session start.
- `CastDynamiteModuleImpl.newReconnectionServiceImpl` still returns `null`, leaving the known sender crash seam when modern framework clients start `ReconnectionService`.

So #580 is not solved on literal master even though multiple open PRs contain working pieces.

## Open-carrier comparison

### Preferred connectionless core: #3567 + #3570

`#3567 @ 3da2c730bbba316035b202ee92302e9c947d0036`
- narrowly fixes framework session start: registers the media-router callback, adds the full-category prefix fallback, and hardens route extras/provider handling;
- reports device/emulator validation up to `SessionManager` CONNECTING.

`#3570 @ da96e2e1880f437a7f349fec9ffbe2f8f82fad98`
- preserves the old Binder ABI and adds connectionless calls as **AIDL ordinals** `connect=16`, `setListener=17`, `unregisterListener=18`, which correspond to Binder transaction numbers 17/18/19;
- adds listener `onConnectedWithResult=13` (Binder transaction 14);
- accepts `CAST_API(161)`, advertises the cxless features, fixes outgoing-vs-inbound send acknowledgement, and adds listener death cleanup;
- reports end-to-end real-hardware playback with Prime Video, Netflix, Disney+, and a minimal sender, including sender-death cleanup.

This pair is the strongest existing basis for modern first-party apps because it addresses both framework session creation and the connectionless device-controller handshake, with actual device evidence.

### Complementary pieces

`#3577 @ e2f880857b7fe92db4f06da42ae7ca64a40da001`
- independent one-file fix: non-null no-op `IReconnectionService` so a connectionless sender does not NPE-loop when the Cast SDK starts the reconnection service;
- reports hardware before/after evidence and is additive to the connectionless core.

`#3554 @ 5523643fd229c0cd2c1b3cde7611d79d7f629aab`
- independent `ModuleDescriptor` for `com.google.android.gms.cast.framework.dynamite`;
- compile-validated and clears the local Dynamite descriptor/temp-fix warning; safe supporting slice, not a #580 solution by itself.

`#3470`
- useful robustness donor for background route socket operations, `GeneralSecurityException` lifecycle, and `joinApplication`; it overlaps the device-controller file and should be reconciled semantically rather than blindly stacked.

### Do not use as the consolidation base

`#3668 @ c50e8e994dde1a1c40832e702ee0e608e181dacb`
- edits only legacy `CastMediaRouteController` and does not implement the connectionless Binder/service path used by modern apps;
- therefore it is not the missing modern-app residual.

`#3781 @ 6a0ac7b8c341f21685748c112d44d686e58bab95`
- substantially duplicates #3567/#3570, has no Chromecast hardware validation in its own receipt, and also carries unrelated/noisy changes (`gradle.properties`, manifest permissions/formatting, `CastDevice.java`).
- prefer the smaller hardware-tested donors rather than widening the merge surface.

`#3802 @ fc69d36e8fd69f25b8cd1c42cad02c8aa21662b9`
- **must not be composed** as written. Its claimed “off-by-one” repair shifts the connectionless AIDL ordinals to 17/18/19 even though #3570’s comments correctly distinguish ordinal 16/17/18 from Binder transactions 17/18/19.
- worse, #3802 rewrites existing legacy controller ordinals (`stopApplication` 4→3), removes `sendMessage=8`, and replaces most listener methods with implicit/order-derived IDs. That is an ABI-breaking change to already-defined Binder transactions, not a narrow connectionless repair.

## Remaining work: consolidation, not another independent Cast implementation

The highest-value distinct contribution is a maintainer-friendly integration carrier built from current master that preserves legacy transaction IDs and combines only the proven orthogonal pieces:

1. framework session-start slice from #3567;
2. connectionless device-controller/service/AIDL slice from #3570;
3. no-op reconnection service from #3577;
4. local Dynamite descriptor from #3554;
5. only the demonstrated lifecycle/join improvements from #3470 that remain necessary after 1–4.

Do **not** merge the legacy route-controller rewrite merely to make the diff larger, and do not take #3802’s Binder renumbering.

## Regression/acceptance matrix for a consolidated carrier

Before an upstream submission, require source-level regression coverage for the contract that has attracted conflicting PRs:

- generated/raw Binder ABI canary: classic ordinals remain unchanged; new controller ordinals are exactly 16/17/18 and readiness callback ordinal exactly 13;
- broker/service canary: both classic `CAST` and connectionless `CAST_API(161)` requests resolve to the controller, with cxless feature advertisement on the modern path;
- handshake canary: out-of-band listener → connect → exactly one `onConnectedWithResult(SUCCESS)`; replacement/dead listener cannot tear down a newer client;
- message canary: outgoing `sendMessage(requestId)` owns send-success correlation; inbound messages are delivered only as inbound text/binary events;
- framework canary: route selection resolves the suffixed session-provider category and starts `SessionImpl` without null/extras crash;
- reconnection canary: dynamite returns a non-null delegate and `onCreate`/`onStartCommand` are safe;
- device acceptance: Prime Video + Netflix + Disney+ + YouTube/minimal sender; route select, app launch, media load/play, control, disconnect, and force-kill cleanup. Record exact APK/source SHA and receiver/device model.

This test matrix is the useful missing piece even if maintainers choose a different source carrier: it prevents a future consolidation from “fixing” the connectionless calls while silently breaking legacy Binder IDs.

## Recommended maintainer proposal

Rather than opening another competing whole-issue PR, ask maintainers to choose a consolidation base (prefer #3567/#3570 because they are narrow and hardware-tested) and permit a current-master integration that absorbs #3577 + #3554 and the necessary #3470 lifecycle pieces. The integration should explicitly close/rebase duplicate carriers instead of re-implementing their work and should add the Binder-ABI regression matrix above.

No sponsor contact, BountyHub claim, external PR, or reward claim was made by this packet.