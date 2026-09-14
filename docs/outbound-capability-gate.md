# Global outbound capability gate

Revenue-bearing sends have two distinct single-writer problems:

1. `concierge.outbound_singlewriter.OutboundSingleWriter` is the durable **local** host/state transition guard.
2. A cross-seat winner must be proven against a provider-linearizable **global** coordination primitive before local `SENDING` is armed.

The Slack `CLAIM` snapshot/election helpers in `outbound_singlewriter.py` are useful advisory coordination and collision telemetry, but Slack search/index snapshots are **not a linearizable mutex** and MUST NOT be the sole authority for a revenue-bearing send.

## Canonical global authority

New send boundaries that require current-worker ownership use Commons `tools/outbound_send_guard/capability_lease.py` (`outbound-send-lease/v2`) in the canonical coordination repository `woahwhattheheck/commons`.

Do not create a second CAS/acquisition implementation in bounty-concierge. Acquisition belongs to Commons. The bounty-concierge `outbound_capability_gate` only consumes and verifies its output.

The v2 capability is private proof of possession. Public lease receipts, tags, Slack messages, logs, and copied winner evidence are insufficient without that capability. Keep the raw 256-bit capability out of Slack, GitHub issues/PR text, provider metadata, argv, logs, and proof receipts.

## Required composition

1. Finish the independent outbound preflight first: owner/content approval, exact route and identity, DNR/cooldown and dedupe checks, approved buyer/offer scope, and any provider-specific gates.
2. Build `build_commons_v2_claim(...)`. Its `repo` is pinned to `woahwhattheheck/commons`; its preflight digest binds the local `operation_key` plus the already-frozen outbound-preflight SHA-256.
3. Acquire the v2 lease with Commons and retain the raw capability privately before provider mutation, following Commons' custody requirements.
4. Call `prepare_send_with_capability_lease(...)` with the public claim/receipt and the privately retained capability.
5. The gate validates the capability commitment **before any provider read**, then re-reads the exact live GitHub ref and annotated tag, validates metadata/tagger/anchor, and only then calls the existing local `prepare_send` transition.
6. A successful gate returns proof with `external_send_authorized: false`. The caller still needs every independent business/content/provider authorization before performing the actual external send.

## Fail-closed boundaries

The gate blocks on copied public winner evidence, wrong/missing capability, caller-selected coordination repo, owner/claimant mismatch, changed local operation or upstream preflight generation, receipt digest tampering, ref/tag read uncertainty, ref/tag drift, duplicate or transplanted metadata, wrong anchor/tagger, or any unsupported schema.

A v2 `HOLD` receipt may recover only when the current worker proves the matching private capability and fresh live ref/tag state proves that same capability-bound tag actually owns the permanent lease.

Buyer/offer scope strings are inputs from a separately authoritative preflight. Passing a syntactically valid scope through the lease does **not** authenticate a buyer, prove relationship authority, or approve an offer.
