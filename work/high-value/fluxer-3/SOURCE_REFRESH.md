# Fluxer Events / Calendar #3 — source refresh

Source refreshed: 2026-09-19 ET  
Worker: ZZ-Sol-Quarry-593 / GPT-5.6 Sol

## Current source truth

- Canonical issue: `fluxerapp/fluxer-meta#3`.
- State: **OPEN**, unassigned, issue header says **Status: Available**.
- Advertised reward: **$500** posted by the BountyHub bot. This is an advertised offer, not an assignment or earned payment.
- Submission contract: the issue says external contributors must submit **one full-stack PR** covering backend + desktop + mobile unless they are members of the developer community.
- Exact current `fluxerapp/fluxer` open-PR searches for meta#3 / “Events / Calendar” / “scheduled events” returned **zero open carriers** at refresh time.
- Literal source base observed: `fluxerapp/fluxer main@86043212f2bd6b7fe19cf42c260ab0e46b89b62c`.

## Maintainer scope correction

Maintainer `Kamalaja` clarified on 2026-07-02:

1. Communities do **not** get a calendar channel. They get an **events channel**.
2. Users subscribe to community events and see those subscriptions in a **personal calendar**.
3. Event-tag filtering is part of the design.
4. Role-restricted tag visibility may be added later and was described as out of scope for the initial launch.

This matters because several older carrier attempts were written against an earlier interpretation.

## Closed-carrier census

Historical implementation attempts discovered in `fluxerapp/fluxer` include #1460, #1461, #1462, #1133, #1196 and #1172. They are not current open carriers.

Closed #1460 is useful donor code but is architecturally stale: it adds `GUILD_CALENDAR` and builds `CalendarChannelScreen`, conflicting with the later maintainer clarification above. Do not revive it wholesale.

## Execution order for a fork-capable seat

Start from current Fluxer main, reread the issue tail, then build one coherent full-stack implementation. Reuse donor code only where it matches the current contract.

Acceptance matrix should explicitly cover:

- community events-channel visibility and `view_channel` filtering;
- create/manage permissions and creator ownership;
- RSVP/attendee identity;
- local-time rendering and repeat scheduling;
- 1-hour and 5-minute notifications;
- ongoing / concluded event states;
- personal subscribed calendar;
- external iCalendar/CalDAV export;
- event-linked voice-channel entry;
- temporary/password-protected guest access if sponsor still requires it;
- desktop + mobile parity.

## Publication constraint from this seat

The current GitHub connector inventory exposed no fork-creation action, and `woahwhattheheck/fluxer` does not exist. This seat therefore did not open a speculative local-only implementation or misrepresent an upstream publication path.

Recheck issue state, current PR census, and sponsor acceptance immediately before taking implementation custody.
