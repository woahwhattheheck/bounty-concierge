# Expensify/App #91148 — secondary-contact deletion / reauthentication source map

Status: current-main source validation only. The upstream issue is already assigned; do not open a competing PR from this packet.

Pinned upstream: `Expensify/App@dd0e8b65546b6e2e8914e74c535da26cd85dacc5` (`main`, observed 2026-09-19 EDT).
Issue: `Expensify/App#91148` — OPEN, `External` + `Help Wanted`, currently assigned to `youssef-lr` and `linhvovan29546`; linked Upwork job exists. The advertised $250 is not an award or payment receipt.

## Current source contract

1. `src/libs/actions/User.ts` blob `c1adf3529dda64b187b36a1c526a5081bd789a7e`
   - `deleteContactMethod()` builds optimistic/success/failure updates only for `ONYXKEYS.LOGINS`.
   - It calls `API.write(WRITE_COMMANDS.DELETE_CONTACT_METHOD, {partnerUserID: contactMethod}, ...)`.
   - The command currently supplies no reauthentication opt-out.

2. `src/libs/API/parameters/DeleteContactMethodParams.ts` blob `274c3ba7351272b0d37e45dc0cffc9dcb800a893`
   - current shape is exactly `{partnerUserID: string}`; no `skipReauthentication` field.

3. `src/libs/API/makeRequest.ts`
   - write requests default `request.data.shouldRetry ??= true` and carry `apiRequestType=WRITE` through the prepared request.
   - therefore the legacy `(!shouldRetry && !apiRequestType)` bypass cannot be reached by a normal `API.write` unless the middleware has another explicit escape hatch.

4. `src/libs/Middleware/Reauthentication.ts` blob `3a90969d3b68cf23ab5aef778cafe18dd9761b3d`
   - the code comment explicitly says creating/deleting logins are examples of requests that should handle the original auth-failure response instead of being reauthenticated/retried.
   - the operative guard is `if ((!shouldRetry && !apiRequestType) || skipReauthentication)`. For current `DeleteContactMethod`, `shouldRetry=true`, `apiRequestType=WRITE`, and `skipReauthentication` is absent, so a body/HTTP 407 reaches `reauthenticate(request?.commandName)`.
   - failed reauthentication ultimately redirects to sign-in.

5. Existing canonical precedent
   - `LogOutParams` already carries optional `skipReauthentication` and the logout action supplies `shouldRetry:false, skipReauthentication:true`.
   - `SignInWithShortLivedAuthTokenParams` also carries the same explicit bypass.
   - a repository-wide current-main search found no test occurrence of `skipReauthentication`; current direct coverage of this bypass is therefore not source-visible through indexed test content.

## Proposal census / collision fence

Recent issue proposals already converge on the same narrow frontend mechanism: add `skipReauthentication` to the delete-contact request rather than globally changing reauthentication behavior. A competing proposal also suggested moving the command to `makeRequestWithSideEffects`, but that does not itself satisfy the guard because a side-effect request still has a non-empty `apiRequestType`; it would also change queue/offline semantics.

Because the issue is now assigned and the narrow repair is already proposed, this lane should **not** publish another upstream implementation or duplicate proposal.

## Distinct validation contribution

The missing acceptance proof is not another prose diagnosis; it is a command-specific regression that proves the existing escape hatch behaves correctly on the actual sequential-write path.

Recommended test boundary (on the selected contributor carrier after assignment):

- construct a `DeleteContactMethod` write request carrying `skipReauthentication:true`;
- feed Reauthentication a body-level 407 and separately an HTTP-level 407;
- assert the middleware does **not** call `reauthenticate`, `Authenticate`, or `redirectToSignIn`;
- assert the original 407 is allowed to continue to `SaveResponseInOnyx` so request failure/finalization semantics still run;
- assert no replay of the delete command occurs after the 407;
- control: identical request without the flag must enter reauthentication, proving the regression is command-bound rather than globally weakening auth handling;
- preserve the request as `API.write`; do not move it to a broader request type solely to evade reauth.

One important follow-up assertion: `SaveResponseInOnyx` applies server `onyxData` first, then request `failureData` when `jsonCode !== 200`. Since current delete `failureData` restores the old login, the assigned implementation should pin the real 407 response contract and verify whether the server considers deletion committed on that response. If it does, a skip-only patch could keep the session alive while temporarily restoring a now-deleted login in local state. That state contract needs a deterministic fixture rather than assumption.

This is a review/test packet only: no production account mutation, no Upwork action, no external proposal, and no competing Expensify PR.