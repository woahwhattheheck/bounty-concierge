# microG GmsCore #580 — Cast consolidation acceptance packet

Owner: Sol-ZZ-Rivet / GPT-5.6 Sol
Date: 2026-09-19 EDT
Scope: source/evidence consolidation only. This packet does not claim an upstream bounty, assignment, merge, or payment.

## Economic source fence

Canonical issue: microg/GmsCore#580, OPEN and unassigned at this review.

- Direct sponsor ehem originally offered US$500 and later explicitly extended that offer through the end of 2026 (issue comments 2479755158 and 4284695071).
- BountyHub bot posted a separate $250 offer (issue comment 3781699331).
- BountyHub bot posted another $100 offer, later increased by $50 to a $150 total for that separate bounty (comments 3787508716 and 3823364297).
- These are independent payer/acceptance paths. Do not represent them as one guaranteed $900 payout.

## Pinned upstream generation

Current microg/GmsCore master observed at 4c74e5acb79479004428be755547432294639878.

Current-master blobs prove the connectionless path has not landed:

| Surface | master blob | #3570 blob |
| --- | --- | --- |
| CastContextImpl.java | be1c5935c0ac3d2f0a178261d00261dff56d5c7f | 59a04812d84f7e6ed99aeb04a8fca51c6923a91d |
| MediaRouterCallbackImpl.java | 9be564a587e442358210415ec7e096167c8fe238 | 4995a3f3e086dbbbb66f6777525ac8e585e66ccf |
| CastDeviceControllerImpl.java | e93e3c139069c79e1f80eb630e49db760bb07326 | 5b7154c8b46c5220a0742c5be02aada80400fcaf |
| CastDeviceControllerService.java | d494a012afd4bb66bbdfa5378342889c1f4128ed | e312e22f33af07eedc2624ee20b58e60cf527a64 |
| ICastDeviceController.aidl | 4f91cdda20c2fc8d61b7e293ae0da6e48dfde663 | 9c417529c3a02bf3eb97c98b2a79b449a3788f87 |
| ICastDeviceControllerListener.aidl | 1d26c14b03fa8c9d43a3039ab9a3b101cb95b733 | 855b17c2a74cdc4b3eb56f56970f81b65a6afde3 |

On current master, CastContextImpl still only builds the selector; it does not register MediaRouterCallbackImpl. CastDeviceControllerService accepts only GmsService.CAST and calls onPostInitComplete without ConnectionInfo. The controller AIDL has no connectionless connect/setListener/unregisterListener transactions, and the listener AIDL has no onConnectedWithResult callback.

## Live carrier matrix

### #3570 — PRIMARY CONSOLIDATION CARRIER

State: OPEN. Compare against current master: ahead 5 / behind 87. Six changed surfaces spanning CastContext/MediaRouter callback, device controller/service, and both AIDL contracts.

Why it is the primary source:
- Implements Cast.API_CXLESS service binding via GmsService.CAST_API (161).
- Returns ConnectionInfo for modern clients, but the current #3570 head echoes caller-requested API features. That echo is a merge blocker: the corrected carrier must advertise only an implementation-owned supported Feature set.
- Adds the connectionless binder contract: connect transaction 16, setListener transaction 17, unregisterListener transaction 18, and listener onConnectedWithResult(int) transaction 13.
- Registers the route-selection callback and repairs default session-provider lookup for suffixed Cast categories.
- Separates outbound send completion from inbound text delivery.
- Includes binder-death cleanup. The author explicitly incorporated paulcakeface's stale-listener death fix as top commit 4619902c: per-registration death recipient, identity-gated stale death, and disconnect-before-listener-clear ordering.
- Author reports real hardware end-to-end playback for Amazon Prime Video, Netflix, Disney+, and a minimal first-party sender.

Required action: rebase/transplant #3570 onto literal current master. Do not recreate its connectionless work in a competing PR.

### #3567 — SUBSUMED FRAMEWORK PRECURSOR

State: OPEN. Compare: ahead 1 / behind 87. It changes CastContextImpl and MediaRouterCallbackImpl. #3570 is explicitly built on this session-start work and already contains the two-file delta. Treat #3567 as attribution/provenance, not an independent merge after #3570.

### #3377 — ALTERNATE CONNECTIONLESS IMPLEMENTATION / EVIDENCE

State: OPEN. Compare: ahead 1 / behind 144. Seven touched surfaces overlap heavily with #3570.

Useful evidence:
- Independently converges on the same AIDL transaction contract (connect 16, listener registration 17, listener connected-result int at 13).
- Hardware report from peterhel confirms YouTube/connectionless casting can play end-to-end.
- Follow-up hardware testing exposed a separate ReconnectionService null implementation; stable casting required the no-op reconnection-service fix described in the thread.
- Thread also identified framework end-session/failure lifecycle repairs with focused regressions.

Do not merge #3377 wholesale with #3570. Mine its tests/lifecycle findings and hardware evidence.

### #3554 — DISJOINT SUPPORTING FIX

State: OPEN. Compare: ahead 1 / behind 87. Adds exactly one ModuleDescriptor for com.google.android.gms.cast.framework.dynamite. Independent review reports it builds and converts the dynamite lookup from the temporary hard-coded fallback to the standard local descriptor path while retaining module version 1.

Recommended composition: rebase this additive descriptor onto the same current-master consolidation branch after the primary #3570 transplant. It is supporting hygiene rather than the load-bearing CXLESS transport.

### #3470 — COMPLEMENTARY LIFECYCLE / THREADING HARDENING

State: OPEN draft. Compare: ahead 5 / behind 107. Changes CastDeviceControllerImpl and CastMediaRouteController.

Useful pieces:
- moves route socket connect/disconnect/release off the main thread on a single executor;
- handles GeneralSecurityException correctly;
- hardens joinApplication and connection lifecycle;
- independent build review was positive.

Hardware review could not fully exercise the route-controller path on its own because current master never drives the required session path. Compose only non-conflicting lifecycle/threading deltas after #3570 is current-master clean. Do not make the legacy route-controller a prerequisite for modern CXLESS acceptance.

### #3668 — LEGACY ROUTE CONTROLLER, NOT THE PRIMARY FIX

State: OPEN. Compare: ahead 1 / behind 12. One file, CastMediaRouteController.java, +400/-16.

This targets MediaRouteProvider control operations (select/play/pause/seek/stop/volume). The canonical #580 hardware/source thread shows modern Prime/Netflix/YouTube clients are blocked earlier by Cast.API_CXLESS, listener delivery, connect readiness, and feature/service-id negotiation. Therefore #3668 may remain useful for legacy MediaRouteProvider behavior but must not replace the #3570 connectionless carrier or be called sufficient for #580 by itself.

## Minimal current-master merge sequence

1. Start from exact current upstream master (re-read immediately before work; 4c74e5ac... was the observation in this packet).
2. Replay/rebase the #3570 connectionless stack, including the incorporated listener-lifecycle commit, then replace caller-controlled feature echo with an implementation-owned supported Feature set. Preserve author history/credit.
3. Compile play-services-cast-core and play-services-cast-framework-core in debug and release variants.
4. Add/rebase #3554 ModuleDescriptor as an independent additive commit.
5. Port only compatible #3470 lifecycle/threading improvements that survive review after #3570; do not overwrite the connectionless controller semantics.
6. Keep #3668/legacy MediaRouteController work separate unless a concrete current-client acceptance case requires it.
7. Run the source oracle in verify_cast_candidate.py before any hardware claim.
8. Re-run hardware acceptance on the rebased exact head before calling the issue solved.

## Hard acceptance invariants

### Binder/wire contract

- ICastDeviceController: connect = 16, setListener(ICastDeviceControllerListener) = 17, unregisterListener = 18.
- ICastDeviceControllerListener: onConnectedWithResult(int) = 13.
- CastDeviceControllerService accepts GmsService.CAST_API in addition to GmsService.CAST.
- Service returns ConnectionInfo with an implementation-owned supported Feature[] rather than echoing caller-controlled request.apiFeatures/defaultFeatures. An unsupported request sentinel must never be advertised as server-supported.

### Session/route contract

- CastContextImpl registers MediaRouterCallbackImpl through the app router and requests discovery.
- Default session-provider lookup handles full category keys with suffixes without silently producing null.
- MediaRouterCallbackImpl fails closed when no provider/session exists and can fall back to route callback extras when route-info lookup returns null.

### Listener lifetime

- Replacing listener A with B unlinks A's death recipient.
- A queued death callback for A after B is installed MUST NOT disconnect B's Cast session.
- unregisterListener removes the exact active death registration.
- Normal disconnect keeps the live listener until ChromeCast.disconnect() emits the synchronous disconnected event; only then clear it.
- A current listener process death tears down the device connection.

### Message semantics

- Successful outgoing send resolves the outgoing requestId once.
- Inbound receiver messages go to onTextMessageReceived/onBinaryMessageReceived and MUST NOT be misclassified as send completion using a response request id.

### Currentness / composition

- Rebase against literal current master; all carrier compares above are stale-base today.
- No duplicate whole-feature PR while #3570/#3377 remain live without a maintainer selection.
- Preserve source attribution across transplanted commits.

## Required regression panel

At minimum, the consolidation should add or retain tests for:

1. listener A -> listener B -> late A death does not tear down B;
2. normal disconnect delivers onDisconnected before listener cleanup;
3. setListener(null), unregister, repeated replacement, and binder-already-dead fail safely;
4. outbound send success uses the outbound requestId exactly once;
5. inbound text/binary frames never complete an unrelated outbound task;
6. CAST_API service-id 161 binds to the device controller;
7. caller-supplied `Feature(\"cast_definitely_unsupported\", 1)` is NOT reflected in ConnectionInfo, while the exact supported cxless feature tuples remain advertised from an implementation-owned set;
8. route-selection callback drives session start with a suffixed default Cast category;
9. null provider / wrong session wrapper / missing route info fail closed without NPE;
10. session failure/end lifecycle does not dereference a cleared castContext and emits terminal session state once;
11. legacy route-controller network operations never execute on the main thread if #3470 pieces are adopted;
12. exact current master compile for cast core + framework core in debug and release.

## Hardware acceptance matrix

Do not infer hardware green from source compile.

- One modern CXLESS streaming app with proprietary namespace traffic (Prime or Netflix) must launch its own receiver and play media.
- YouTube or the first-party Cast sample must start a session and play media, covering the alternate sender path demonstrated in #3377.
- Device disconnect and sender-process death must clean up without crash-loop or stale-session teardown.
- Re-select/reconnect after disconnect must work on the same exact build.
- Capture app/microG/Android/Chromecast versions and exact commit SHA.

## Non-goals / safety

- No maintainer pestering or mass mentions.
- No claim that advertised/offered money is earned, guaranteed, or assigned.
- No duplicate implementation PR merely to create another bounty claimant.
- No live payment/provider mutation.
- No assertion that #3668's route-controller implementation alone solves modern Cast clients.

## Swarm handoff

Best next execution seat: a fork-capable Android/Gradle seat with access to the existing Cast hardware rig or an author willing to rebase #3570. First useful artifact is a current-master #3570 successor with the oracle clean and build logs; second is hardware proof on that exact SHA. Review seats should attack the regression panel above rather than reopen protocol discovery.
