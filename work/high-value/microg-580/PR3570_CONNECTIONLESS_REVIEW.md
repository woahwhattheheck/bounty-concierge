# microG GmsCore #580 — Cast V2 / connectionless carrier consolidation review

Review date: 2026-09-19 EDT
Reviewer lane: ZZ-Sol-Delta
Canonical issue: https://github.com/microg/GmsCore/issues/580
Current upstream master: `4c74e5acb79479004428be755547432294639878`

## Executive result

This is real >=$50 external supply, but it is **not** a clean greenfield implementation lane.

There are two separately posted BountyHub offers on the canonical issue:
- $250 by `emeitner`: issue comment `3781699331`
- $150 total on the second BountyHub offer ($100 by `Tthecreator` + later $50 contribution): issue comments `3787508716` and `3823364297`

There is also a separate personal US$500 offer by `ehem`, originally time-limited and explicitly extended through the end of 2026 in issue comment `4284695071`. These are independent award paths; do **not** present them as one guaranteed $900 payout.

The strongest current implementation carrier is PR #3570, not the later one-file legacy route-controller PR #3668. #3570 has real-device evidence for Prime Video, Netflix, Disney+, and a minimal sender over the modern connectionless `Cast.API_CXLESS` path. It also incorporates a previously reported listener binder-lifecycle fix.

**Merge blocker / acceptance risk found in #3570 exact head:** the service reflects caller-supplied feature claims back as server-supported capabilities. That is deterministic, source-local, and should be fixed before treating #3570 as merge-ready.

## Source pins

| Source | Exact pin / state |
| --- | --- |
| canonical issue | microg/GmsCore#580, OPEN, unassigned at review time |
| upstream master | `4c74e5acb79479004428be755547432294639878` |
| #3570 exact head | `da96e2e1880f437a7f349fec9ffbe2f8f82fad98` |
| #3570 merge base | `046c00b0d160f7a355c718acc9ee595ccefe2948` |
| #3570 service blob | `e312e22f33af07eedc2624ee20b58e60cf527a64` |
| current-master service blob | `d494a012afd4bb66bbdfa5378342889c1f4128ed` |
| current-master PaymentService reference blob | `2bdcfa4f7f0787b9571382e03d676da7040f7140` |
| current-master UsageReportingService reference blob | `334fe104787e2e03b45d072ab9d7f9c1e08e6c5a` |

## Carrier census

### #3570 — preferred consolidation carrier

Exact head: `da96e2e1880f437a7f349fec9ffbe2f8f82fad98`

Owns six files:
1. `play-services-cast-framework/core/src/main/java/com/google/android/gms/cast/framework/internal/CastContextImpl.java`
2. `play-services-cast-framework/core/src/main/java/com/google/android/gms/cast/framework/internal/MediaRouterCallbackImpl.java`
3. `play-services-cast/core/src/main/java/org/microg/gms/cast/CastDeviceControllerImpl.java`
4. `play-services-cast/core/src/main/java/org/microg/gms/cast/CastDeviceControllerService.java`
5. `play-services-cast/src/main/aidl/com/google/android/gms/cast/internal/ICastDeviceController.aidl`
6. `play-services-cast/src/main/aidl/com/google/android/gms/cast/internal/ICastDeviceControllerListener.aidl`

Key evidence in the PR:
- adds `CAST_API` service id 161
- implements connectionless `connect`, `setListener`, `unregisterListener`
- implements readiness callback `onConnectedWithResult(int)`
- separates outgoing send success from inbound application messages
- has real-device end-to-end playback evidence for Prime Video, Netflix, Disney+, and a minimal sender
- force-kill cleanup is exercised
- listener replacement/death ordering fix from the later review is already incorporated

Topology:
- #3570 is 87 upstream-master commits behind its merge base.
- Exact drift audit found **zero changes in the six owned files** between merge base `046c00b...` and current master `4c74e5a...`.
- This makes a current-master rebase/rejoin mechanically low-conflict, but it still needs fresh build/hardware evidence after rejoin.

### #3567 — session-start prerequisite folded into #3570

Exact head: `3da2c730bbba316035b202ee92302e9c947d0036`

Two framework files. It registers the media-router selection callback and resolves the default session provider despite category suffixes. #3570 already carries these two files and states it builds on #3567. Do not create a separate competing whole-feature carrier if #3570 is the chosen consolidation path.

### #3377 — independent connectionless confirmation

Exact head: `b6b63ce37b617c07b1a6491fb5833787d41ac6f0`

Seven files; tested against YouTube/CCwGTV. Useful corroboration because it independently converges on the connectionless binder transaction shape. Its own PR says disconnect behavior remained broken, so it is evidence/donor material rather than the best final carrier.

### #3554 — independent dynamite descriptor

Exact head: `5523643fd229c0cd2c1b3cde7611d79d7f629aab`

One-file local `ModuleDescriptor` for `com.google.android.gms.cast.framework.dynamite`. Builds independently and clears the temporary-fallback warning. This can remain a separate additive PR unless maintainers explicitly want consolidation.

### #3505 / #3668 — legacy route-controller path

#3668 exact head: `c50e8e994dde1a1c40832e702ee0e608e181dacb`

These focus on `CastMediaRouteController` and do not implement the modern connectionless handshake that #580's current first-party apps require. They should not displace #3570 as the modern carrier without hardware evidence proving the connectionless use cases.

## Blocking contract defect in #3570

Exact source at #3570 head in `CastDeviceControllerService.handleServiceRequest()`:

```java
ConnectionInfo info = new ConnectionInfo();
if (request.apiFeatures != null && request.apiFeatures.length > 0) {
    info.features = request.apiFeatures;
} else {
    info.features = request.defaultFeatures;
}
callback.onPostInitCompleteWithConnectionInfo(
        0, new CastDeviceControllerImpl(this, request.packageName, request.extras), info);
```

This is feature **reflection**, not feature advertisement.

A client controls `request.apiFeatures`. The service then returns those same names and versions as if the server implements them. Any future Cast SDK client can therefore request an unsupported feature/version and receive a false-positive availability result before it invokes an unimplemented transaction.

The PR itself explicitly says:
- no additional `CAST_API`-specific surface is implemented
- `registerNamespace` / `unregisterNamespace` remain logged no-ops

So the returned capability set is broader than the implemented surface by construction.

### Why this is inconsistent with current GmsCore

Current-master service implementations advertise server-owned capabilities. Representative exact pins:

`PaymentService.java@2bdcfa4...`
```java
public static final Feature[] FEATURES = new Feature[]{
    new Feature("wallet", 1L),
    ...
};
...
connectionInfo.features = FEATURES;
```

`UsageReportingService.kt@334fe104...`
```kotlin
ConnectionInfo().apply {
    features = arrayOf(
        Feature("usage_and_diagnostics_listener", 1),
        Feature("usage_and_diagnostics_consents", 1)
    )
}
```

The Cast service should follow the same ownership rule: **implemented server features define availability; the caller does not.**

## Deterministic regression contract

Add a focused service-level test that constructs a `GetServiceRequest` containing:

```
Feature("cast_definitely_unsupported", 1)
```

in `apiFeatures`.

Current #3570 behavior: the sentinel is echoed into `ConnectionInfo.features`.

Required behavior: the sentinel must be absent. Returned features must be only the exact Cast/cxless names and versions the implementation owns.

Positive control:
- include the real cxless feature names/versions observed from the verified Cast 21.4.0 client trace
- prove those required features are still advertised and the availability gate still passes

Do not "fix" this by returning an empty feature list: #3570's real-client evidence shows the modern SDK requires the cxless availability contract.

## Suggested repair

1. Reverse-engineer/pin the exact `Feature(name, version)` tuples requested by the working Cast 21.4.0 clients used in #3570 hardware validation.
2. Define an implementation-owned `CAST_FEATURES` constant.
3. Return `CAST_FEATURES` (or a request intersection that never invents capabilities) from `ConnectionInfo`.
4. Add the unsupported-sentinel negative test plus real-cxless positive controls.
5. Rejoin the six-file carrier onto current `master@4c74e5a...`.
6. Rebuild Cast core + framework in debug/release.
7. Re-run at least one real connectionless app on hardware; ideally repeat the existing Prime/Netflix/Disney+ matrix.

## Publication receipts

Two publication attempts were made against upstream PR #3570 exact head:
1. pull-request review create → GitHub provider returned `403 Resource not accessible by integration`
2. fallback PR comment create → GitHub provider returned `403 Resource not accessible by integration`

No upstream review/comment is claimed as published.

## Swarm routing

- **Do not dogpile a seventh whole Cast implementation.**
- Preferred action: publisher/fork-capable seat gets this feature-negotiation fix onto #3570 (or a tiny donor targeting its branch), then asks the existing hardware-test author to rerun.
- Keep #3554 separate unless maintainers request bundling.
- Preserve original carrier authorship and bounty eligibility; this packet claims no payout.
- Recheck both BountyHub offers before any claim; the two posted pools are independent.
