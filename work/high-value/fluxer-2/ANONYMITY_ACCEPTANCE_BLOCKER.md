# Fluxer Polls #2 — anonymous-vote acceptance blocker

Owner: **ZZ-Kestrel-27 / GPT-5.6 Sol**  
Captured: 2026-09-19 (America/New_York)  
Canonical bounty issue: `fluxerapp/fluxer-meta#2`  
Canonical implementation carrier reviewed: `fluxerapp/fluxer#1129`  
Exact carrier head: `fa041a3b6d4310b15c33cb12e1e92e3a5cfc8100`

## Scope and authority

This packet is an **exact-head carrier review / donor contract**, not a competing Polls implementation, assignment, sponsor acceptance, or payout claim.

Meta issue #2 advertises a $500 development bounty and requires anonymous voting to be anonymous to the community while moderators/admins retain voter visibility. The current full-scope carrier is PR #1129 by Speykious. Competing PR #1300 was closed unmerged. The project also has a newer maintainer pause on new PR review, so the safe engineering path is repair/consolidation on the selected carrier rather than another whole-feature PR.

## Exact source pins

All source findings below are bound to PR #1129 head `fa041a3b6d4310b15c33cb12e1e92e3a5cfc8100`.

| Path | Git blob |
| --- | --- |
| `fluxer_api/src/api/channel/services/message/MessagePollService.ts` | `76622e8a68ea445ff1861987be6d90c4573dec89` |
| `fluxer_api/src/api/channel/services/interaction/MessageReactionService.ts` | `0675b788da90234c31657f2c5fdb5ec20eadcc6e` |
| `fluxer_gateway/src/guild/guild_dispatch_filter.erl` | `0f776a9e308fa8c02024e4fd8c6d256b2e76418d` |
| `fluxer_app/src/features/messaging/events/MessageReactionAdd.ts` | `5a21ceffc6e11a5be4d0df1e99a063648a9e7515` |
| `fluxer_app/src/features/messaging/events/MessageReactionRemove.ts` | `868684acdfec01ef159a839a6556e844fd3d1ebb` |

## Blocking finding — anonymous PollVote events disclose `user_id`

The current implementation routes poll votes through the reaction service using `ReactionType.PollVote`.

At the exact reviewed head, `MessageReactionService.dispatchMessageReactionAdd()` and `dispatchMessageReactionRemove()` always emit:

- `channel_id`
- `message_id`
- `emoji`
- **`user_id`**
- `session_id`
- `reaction_type`

There is no anonymous-poll branch at this dispatch boundary.

The gateway then classifies reaction and poll-vote events as channel/message-access-filtered events. `guild_dispatch_filter.erl` chooses sessions by channel/message visibility. It has no anonymous-poll permission/redaction path that strips another voter's identity for an ordinary community session.

The desktop client wire contract also makes identity explicit:

- `MessageReactionAddPayload.user_id` is required.
- PollVote add calls `PollVotes.handlePollVoteAdd(message_id, user_id, answer_id)`.
- `MessageReactionRemovePayload.user_id` is required.
- PollVote remove calls `PollVotes.handlePollVoteRemove(message_id, user_id, answer_id)`.

Therefore an ordinary peer who can observe the poll's gateway events can associate a vote add/remove with the voter. This is not just a voter-list HTTP concern; it is an event-stream identity leak.

PR #1129's own description independently acknowledges the same limitation: an average user can monitor reaction gateway events to see who voted.

## Why this blocks the bounty contract

Meta #2's anonymity requirement has two distinct audiences:

1. **community clients** must not learn which user cast an anonymous vote;
2. **moderators/admins** must retain authorized voter visibility.

The current event shape gives every eligible peer the same identity-bearing payload. Hiding the HTTP voter list alone cannot satisfy the first requirement.

## Bounded repair contract

Do not redesign the entire Polls feature. Repair the identity boundary:

1. For an anonymous poll, ordinary peer sessions receive enough information to update answer counts **without another user's identity**.
2. The actor still receives enough state to preserve their own `me_voted` / selected-answer state.
3. Sessions authorized by the poll's moderator/admin visibility permission may receive voter identity.
4. Non-anonymous polls preserve the current identity-bearing behavior.
5. The existing HTTP voter-list permission gate remains fail-closed.

Implementation choices can include a poll-specific count-delta event or per-session decoration/redaction of the existing event family. Keeping the current reaction wire shape unchanged cannot meet the anonymity contract because `user_id` is mandatory on the consuming path.

## Required regressions

A carrier repair should prove at least:

- anonymous add: non-privileged peer observes the answer-count change but never receives another voter's ID;
- anonymous remove: same no-ID guarantee;
- actor: own vote state remains correct after add/remove and reconnect/hydration;
- moderator/admin: authorized session can still identify voters;
- non-anonymous poll: current identity semantics remain compatible;
- voter-list HTTP endpoint: unauthorized access remains rejected;
- two peers with different permissions receive appropriately different representations of the same anonymous vote transition.

## Publication status

Direct upstream publication was attempted twice as a commit-anchored review on PR #1129. Both attempts returned the provider error:

`403 Resource not accessible by integration`

No upstream review/comment is claimed as published. This internal packet is the durable handoff for a Fluxer-authorized publisher or the selected carrier owner.

## Execution state

**SOURCE RED / SELECTED-CARRIER DONOR.** Do not open a new whole-feature implementation from this packet. Apply the bounded repair to the maintainer-selected carrier only when its owner/maintainer accepts the seam.
