# MOVA bounty factory

`concierge.mova_factory` turns one **already validated, canonically open, paid** bounty candidate into a deterministic execution packet for the swarm. It is the handoff layer between bounty intake and source execution; it deliberately performs no provider reads or writes itself.

The model is the fast MOVA pattern: keep discovery, implementation, acceptance review, publication, and collection distinct enough to avoid collisions, but bind every stage to one target identity and one source generation so work can flow without repeatedly rediscovering or rebuilding it.

## Inputs and safety fence

A candidate must include the canonical GitHub issue identity, a green paid platform, reward at or above the configured active-work floor, canonical capture and source-generation SHA-256 digests, a `bounty-work-order-lease/v1` receipt that is still `READY`, and the compensation claim that must survive publication. An optional expected Git head pins the source carrier.

**Sponsor archive fence:** intake must directly check the canonical sponsor repository's current GitHub `archived` state, and set candidate `"repository_archived": false` **only after a real provider read**. The factory rejects `true`, absent, or string-valued states instead of inferring that an open issue and bounty label permit publication. The output retains `repository_archived: false` for the publishing handoff. This is an offline assertion from the supplied intake; a publisher must still refresh the sponsor state immediately before a real PR write. Archived sponsors require unarchiving or a separately authorized submission route; retrying other credentials does not fix repository policy.

The compiler rejects stale leases, source-generation drift, mismatched canonical URLs, below-floor work, and required compensation text containing waiver/forfeit language. The effective USD floor is **never below $15** on already-green platforms: callers may raise it for stricter cohorts, but zero, negative, NaN, infinity and non-Decimal overrides are rejected by packet and batch compilation. Currency snapshots are also bounded before decimal fixed-point rendering: source values have at most 64 numeric characters, and rewards or caller-selected floors above $1 trillion are rejected. This prevents finite inputs such as `1e100000` from expanding into huge packets or blocking swarm workers. This is an eligibility floor, not evidence that any advertised bounty will be awarded. It never creates a claim, PR, provider application, payment action, or source mutation.

## Pipeline

Each packet has a deterministic `MOVA-...` operation ID and five role leases:

1. `SCOUT` — owns the fresh canonical/provider fence and hands off `SCOUT_RECEIPT`.
2. `BUILD` — one active source builder on the routine work account; starts only from the accepted scout receipt and fresh head.
3. `QA` — checks the changed behavior and issue acceptance criteria; it does not create a duplicate implementation.
4. `PUBLISH` — publishes exactly the accepted head using the submission account and preserves original contributor credit plus compensation/claim metadata.
5. `COLLECT` — consumes the publication receipt and records provider registration, award/invoice state, and payout receipt without rewriting source.

The packet makes normal routing explicit: routine source work defaults to `tokenjunkielabs`; publication and collection default to `woahwhattheheck`. Role owners are separate from account routing and default to `UNASSIGNED`, allowing the coordinator to spread work across the fleet without silently inventing an owner.

## Current issue evidence permits optional independent QA

The existing five-stage MOVA handoff remains the default. If a fresh canonical issue review
confirms no independent QA seat is required, add the opt-in qa_handoff object to the
otherwise verified READY candidate (not to the provider or its original PR):

    "qa_handoff": {
      "schema": "mova-independent-qa/v1",
      "separate_qa_required": false,
      "issue_url": "https://github.com/owner/repo/issues/42",
      "capture_sha256": "<actual canonical_capture_sha256>",
      "checked_at": "<actually observed timezone-aware timestamp within 6 hours>",
      "builder_focused_check": true
    }

This source-bound intake assertion must refer to the SAME canonical issue and capture.
The source digest must match the READY candidate and the timestamp must be fresh,
never future dated. An explicitly assigned QA owner cannot be silently dropped.
No opt-in or unclear acceptance criteria retains the exact prior five-stage packet.

The alternative produces SCOUT -> BUILD -> PUBLISH -> COLLECT; PUBLISH depends on
the actual BUILD_RECEIPT, not a fabricated QA_ACCEPT_RECEIPT. The builder remains
responsible for focused tests/review required by the changed behavior and sponsor.
Authors, original contribution claims, exact heads, payout paths and provider
permissions are unchanged. The operation ID stays stable, but packet_sha256
changes with the opt-in evidence and role dependencies. This makes no live calls.
## CLI

```bash
concierge-mova candidate.json --owners owners.json --output packet.json
```

`owners.json` is optional and may contain any subset of `SCOUT`, `BUILD`, `QA`, `PUBLISH`, and `COLLECT`. Unspecified roles remain `UNASSIGNED` until coordination assigns them.

The output is stable for identical inputs: both the operation ID and packet SHA-256 are derived from canonical content, which gives Slack/GitHub coordinators one collision key to search before taking a role.

## Throughput rule

This factory is a pipeline, not a ceremony gate. Intake can continue discovering the next bounty while builders implement already-cleared packets, QA checks completed heads, publishers spend scarce authenticated writes only on accepted heads, and collection follows published submissions. Focused validation remains attached to the changed behavior; the factory does not ask for broad suites.
