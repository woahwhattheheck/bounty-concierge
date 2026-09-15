# Canonical revenue email touch identity

Revenue email needs a stricter identity boundary than the generic provider-neutral `outbound_singlewriter.normalize_identity()` API.

The generic single-writer guard deliberately does **not** guess provider address semantics. That is correct for Slack, GitHub, forms, and arbitrary provider destinations, but it creates a dangerous seam for email: `outbound_dedupe` already treats recipient casing and IDNA domain aliases as one operational recipient. If two seats use different presentation strings, they must not be able to derive two valid outbound operation keys before either provider receipt exists.

Use `concierge.outbound_email_identity.normalize_email_touch_identity()` for every revenue-bearing email touch before shared coordination, the local single-writer lease, and the Commons v2 capability-possession gate.

```python
from concierge.outbound_email_identity import normalize_email_touch_identity

identity = normalize_email_touch_identity(
    recipient="Buyer@EXAMPLE.COM",
    offer_key="paid-pilot:q4",
    action="initial-outreach",
)

operation_key = identity.key
# identity.canonical can be passed to OutboundSingleWriter.acquire(...).
```

For the local lease step, `acquire_email_touch(...)` is the convenience wrapper. It does **not** authorize a provider send.

## Identity contract

The email touch key is intentionally provider-neutral. Its canonical identity is:

- `provider = "email-outreach"` — fixed logical authority domain, not Gmail/Outlook/etc.;
- `destination` — the exact recipient policy already used by `outbound_dedupe`: case-folded local part plus IDNA/lowercased domain;
- `thread = "offer:<offer_key>"` — the stable lowercase offer/campaign key, never a seat-local thread/lead identifier;
- `operation` — a normalized NFKC/case-folded machine action such as `initial-outreach`, `proposal`, or `follow-up-1`.

Therefore all of the following collide as the same recipient for the same offer/action:

- `Buyer@EXAMPLE.COM`
- `buyer@example.com`
- Unicode and equivalent IDNA ASCII spellings of the same domain

Changing recipient, offer key, or business action creates a distinct operation key.

The local part case-fold is an **operational dedupe identity policy**, not a claim about SMTP mailbox equivalence. It is intentionally inherited from `outbound_dedupe` so the pre-send provider census and the single-writer/capability stack cannot disagree about exact recipient identity.

## Required production order

For revenue-bearing email, the canonical identity is only one fence. The safe path remains:

1. establish owner/content/route/contact authority and the exact stable `offer_key` + `action`;
2. compute the canonical email touch identity from this module;
3. run provider-truth duplicate suppression across the trusted complete send-capable provider inventory;
4. use fleet/Muse arbitration and durable coordination for collision visibility;
5. acquire the local single-writer lease using the canonical identity;
6. acquire and prove possession of the Commons `outbound-send-lease/v2` capability for the **same operation key** and frozen preflight generation;
7. enter local `SENDING` only through `prepare_send_with_capability_lease(...)` as documented by `OUTBOUND_SINGLEWRITER.md` / the capability-gate docs;
8. perform at most one provider mutation;
9. retain the provider receipt and durable terminal custody evidence.

A `CLEAR` duplicate decision, a local HELD lease, a shared-election win, a Muse selection, or possession of the Commons capability is never by itself content/contact/send/payment authority.

## What this closes

Without this adapter, simultaneous workers can both see an empty provider history and then split the authority domain only because one wrote `Buyer@EXAMPLE.COM` and another wrote `buyer@example.com`. A correct mutex cannot serialize two different keys.

With this adapter, exact-email aliases converge **before** local/shared/global single-writer authority is evaluated, so the second worker contends on the same operation key.

## What this does not close

This module does not decide whether two different addresses belong to the same human or company, does not merge company aliases, and does not serialize Slack/GitHub/form contact against email. Cross-person/company/cross-surface contact policy remains a separate higher-level authority problem.

It performs no email search, send, mailbox mutation, Slack mutation, GitHub mutation, payment action, customer contact, award inference, cash recognition, or revenue recognition.

Operation: `BOUNTY-OUTBOUND-EMAIL-TOUCH-IDENTITY-ZOLX6Q8-20260915` — Z-OsmiumLattice-X6Q8 / GPT-5.6 Sol.
