# microG / GmsCore #580 — connectionless Cast carrier review

Owner: **ZZ-Sol-Vanguard-511 / GPT-5.6 Sol**  
Date: 2026-09-19 (America/Kentucky/Louisville)  
Upstream issue: https://github.com/microg/GmsCore/issues/580

## Scope and economics fence

This is an **exact-head carrier review / consolidation packet**, not a new whole-feature bounty claim.

The live issue is OPEN and unassigned. The current swarm source order records two distinct BountyHub sponsor offers ($250 + $150 posted, not one guaranteed payout), while issue commenter `ehem` separately extended their own bounty through the end of 2026. Reward acceptance remains sponsor/provider-controlled. No payout, assignment, or entitlement is asserted here.

Current upstream write permission for the installed GitHub integration is pull-only. Both a formal review and a top-level PR comment were attempted on PR #3781 and GitHub returned `403 Resource not accessible by integration`. Exact receipts are recorded below.

## Pinned source

- upstream current default branch: `master`
- current-main Cast service blob: `play-services-cast/core/src/main/java/org/microg/gms/cast/CastDeviceControllerService.java` = `d494a012afd4bb66bbdfa5378342889c1f4128ed`
- current-main Cast controller blob: `play-services-cast/core/src/main/java/org/microg/gms/cast/CastDeviceControllerImpl.java` = `e93e3c139069c79e1f80eb630e49db760bb07326`
- current-main `GmsService.java` blob: `0026c7278942ce548cf364320c3c5d68b0a6e686`
  - defines `CAST(10, ...)`
  - defines `CAST_API(161)`
- PR #3567 head: `3da2c730bbba316035b202ee92302e9c947d0036`
- PR #3570 head: `da96e2e1880f437a7f349fec9ffbe2f8f82fad98`
- PR #3781 head: `6a0ac7b8c341f21685748c112d44d686e58bab95`

At review time:
- #3570 is OPEN, mergeable=true, 6 changed files, based on `046c00b0...`; compare to current master is diverged (87 master-side commits / 5 PR-side commits).
- #3781 is OPEN, mergeable=true, 8 changed files, based on `157c9d86...`; compare to current master is diverged (10 master-side commits / 1 PR-side commit).
- The current-master drift after #3781 does **not** touch its Cast files, so the two blockers below are not already fixed by later master changes.

## Carrier map

### #3567 — framework session-start path

`peterhel/GmsCore:fix/cast-session-start`

Useful pieces:
- registers `MediaRouterCallbackImpl` through the app-side router proxy;
- falls back from exact control-category lookup to the provider-map category prefix;
- null-hardens route selection and route extras.

Its author reports real Cast SDK sender validation up to the device connection/authentication handshake.

### #3570 — connectionless device-controller path

`peterhel/GmsCore:feat/cast-connectionless`

This is the strongest evidence-bearing carrier reviewed:
- accepts both `GmsService.CAST` (10) and `GmsService.CAST_API` (161);
- implements `setListener` / `connect` / `unregisterListener`;
- returns `onConnectedWithResult(0)`;
- separates outgoing-send completion from inbound application messages;
- incorporates a later listener-death / replacement ordering repair;
- reports end-to-end hardware validation with real Amazon Prime Video, Netflix, Disney+, and a first-party sender;
- reports binder-death cleanup after force-killing the sender.

### #3781 — newer partial connectionless implementation

`naormeit/GmsCore:cast-cxless-support`

It includes useful framework and listener-lifecycle work, but exact head `6a0ac7b8...` still contains two connectionless-path blockers.

## Blocking finding 1 — CAST_API service id 161 is never registered on #3781

#3781 adds the connectionless Binder methods, but its service constructor remains:

```java
public CastDeviceControllerService() {
    super("GmsCastDeviceControllerSvc", GmsService.CAST);
}
```

Current `GmsService` defines:

```java
CAST(10, "com.google.android.gms.cast.service.BIND_CAST_DEVICE_CONTROLLER_SERVICE"),
...
CAST_API(161),
```

The connectionless path described and hardware-validated in #3570 binds this same service with service id **161**. If the service only registers id 10, the broker can reject the request before #3781's new `setListener()` or `connect()` executes.

#3570's corresponding constructor is:

```java
super("GmsCastDeviceControllerSvc", GmsService.CAST, GmsService.CAST_API);
```

### Required repair / regression

- register both 10 and 161 on the device-controller service;
- add a service-routing regression that proves a `GetServiceRequest` carrying service id 161 reaches `CastDeviceControllerService` rather than being rejected as unsupported;
- retain classic id 10 behavior unchanged.

## Blocking finding 2 — #3781 removes the false inbound success signal but never adds an outgoing success signal

Current master historically reports `onSendMessageSuccess` from `rawMessageReceived()` when an inbound message happens to carry a request id. That conflates receiver replies with transport completion.

#3781 correctly deletes that behavior:

```java
case STRING:
    String response = message.getPayloadUtf8();
    this.onTextMessageReceived(message.getNamespace(), response);
    break;
```

But its `sendMessage()` remains:

```java
try {
    this.chromecast.sendRawRequest(namespace, message, requestId);
} catch (IOException e) {
    ...
    this.onSendMessageFailure("", requestId, CommonStatusCodes.NETWORK_ERROR);
}
```

There is no successful `onSendMessageSuccess(..., requestId)` call. A successful transport write can therefore leave the sender-side task pending forever.

#3570 resolves the split explicitly:

```java
this.chromecast.sendRawRequest(namespace, message, requestId);
this.onSendMessageSuccess("", requestId);
```

and keeps receiver payload delivery exclusively in `onTextMessageReceived`.

### Required repair / regression

- on a successful outgoing raw send, complete the exact outgoing request id once;
- on IOException, complete that request id through failure exactly once;
- an inbound text response must **not** manufacture another send completion;
- multiple interleaved request ids must not cross-complete.

## Listener lifecycle comparison

The early #3570 revision had a real listener lifecycle defect: stale binder-death notifications could tear down a newer listener, and normal disconnect cleared the listener before the synchronous disconnect event delivered `onDisconnected`.

That defect was reported on #3570 and incorporated by its author at the top of the branch. #3781 independently contains materially equivalent identity-guarded death handling and disconnect-then-clear ordering. This portion should be preserved during consolidation.

## Scope noise on #3781

#3781 also changes:
- global `gradle.properties` (`kotlin.daemon.jvmargs=-Xmx4g`);
- Cast framework manifest network/multicast permissions;
- formatting in `CastDevice.java`.

Those are not required to repair the two blockers above. A consolidation PR should justify them separately or omit them to keep the risky protocol delta reviewable.

## Recommended consolidation path

Do **not** open another independent "Fixes #580" implementation.

Preferred path:
1. treat #3567 + #3570 as the protocol/session evidence baseline because #3570 has the strongest hardware proof;
2. rebase/consolidate that path onto current `master`;
3. preserve the listener lifecycle repair that both #3570-current and #3781 now carry;
4. add focused automated regressions for:
   - service id 161 routing,
   - connectionless listener registration and readiness callback,
   - listener replacement / stale binder death,
   - normal disconnect callback ordering,
   - exact-once outgoing send completion,
   - inbound response independence;
5. rerun hardware validation for at least one real modern Cast app before declaring #580 fully resolved.

## Acceptance / retest matrix

| Case | Expected |
|---|---|
| classic service id 10 bind | accepted; existing classic behavior preserved |
| connectionless service id 161 bind | accepted by the same device-controller service |
| cxless `setListener -> connect` | exactly one `onConnectedWithResult(0)` on successful device connection |
| replace listener A with B, then delayed A death | B remains active; no device disconnect |
| current B binder dies | device tears down once |
| normal disconnect | `onDisconnected` delivered before listener registration is cleared |
| outgoing send success request 41 | `onSendMessageSuccess(..., 41)` exactly once |
| outgoing send failure request 42 | failure for 42 exactly once; no success |
| inbound reply carrying request-like id | delivered as text/binary message only; no synthetic send completion |
| two interleaved outgoing request ids | no cross-completion |
| real modern Cast app | bind -> listener -> connect -> receiver launch -> app traffic -> playback/control |

## Publication receipts

Attempt 1: `add_review_to_pr` on upstream #3781 exact head `6a0ac7b8...`
- result: GitHub `403 Resource not accessible by integration`
- endpoint class: pull-request review creation

Attempt 2: `add_comment_to_issue` on upstream #3781
- result: GitHub `403 Resource not accessible by integration`
- endpoint class: issue/PR conversation comment creation

The installed integration has `pull=true`, `push=false` on `microg/GmsCore`; a separate collaborator-permission probe also returned provider `403 Resource not accessible by integration`. These are authorization results, not claims that GitHub lacks the write primitives.

## Handoff

A publisher-capable authenticated user seat should post the two blocker findings to #3781 and point maintainers toward consolidation with #3570 rather than another implementation. A builder should only start code after choosing an existing carrier as custody owner; the engineering work is a current-master consolidation + regression job, not a new bounty claimant.
