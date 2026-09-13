# Superteam Earn agent opportunity provider

`concierge.superteam_provider` adds a **read-only first-party work source** to bounty-concierge. It consumes Superteam Earn's official agent endpoints and normalizes only listings that the provider says accept agent submissions.

This is a discovery rail, not a submission or accounting rail.

## What it reads

The official Superteam agent protocol exposes:

- `GET /api/agents/listings/live` for live `bounty`, `project`, and `hackathon` listings;
- `GET /api/agents/listings/details/<slug>` for the public listing contract;
- `agentAccess = AGENT_ALLOWED | AGENT_ONLY` as the provider eligibility boundary.

The upstream live query additionally filters for published, active, public, non-archived, `OPEN` listings from verified sponsors. The provider revalidates the security-relevant fields it receives rather than assuming the endpoint contract can never drift.

## Credential boundary

Set the agent API key only in the runtime environment:

```bash
export SUPERTEAM_EARN_API_KEY='sk_...'
```

The CLI deliberately has **no plaintext `--api-key` argument**. The bearer value is never included in receipts, errors, normalized rows, or summaries.

Requests are sent only to fixed `https://superteam.fun` endpoint constants and use `allow_redirects=False`, preventing a redirect response from forwarding the bearer credential to a different host.

This module does **not** register a Superteam agent. Agent registration creates external provider state and is outside this read-only surface.

## Discover agent-eligible work

```bash
python -m concierge.superteam_provider list --take 20 --json
python -m concierge.superteam_provider list --type bounty --take 50 --max-batches 3 --json
python -m concierge.superteam_provider list --deadline 2026-10-01 --json
```

The upstream API caps a page at 50. This client caps one discovery receipt at five pages / 250 rows and uses the provider's `excludeIds[]` mechanism for bounded pagination. If the final allowed page is full, the receipt reports `truncated=true`; it never pretends a bounded scan was exhaustive.

Without an explicit `--deadline`, the client uses the current UTC date as the provider deadline floor so expired work is not intentionally requested.

## Fetch one public listing contract

```bash
python -m concierge.superteam_provider details build-and-demo-a-mermail-agent-skill --json
```

The details endpoint is rechecked locally for:

- `OPEN` status;
- `AGENT_ALLOWED` or `AGENT_ONLY`;
- verified sponsor;
- `isPrivate = false`;
- `isPublished = true`;
- canonical slug format.

Skills and eligibility questions are structurally normalized. Sponsor-authored description and requirements are returned only under `public_contract.untrusted_scope` and explicitly carry `scope_text = sponsor_supplied_untrusted_data`. They are data for later evaluation, not instruction authority for the local runtime.

## Compensation semantics

Superteam listings can use tokens such as USDC or USDG and can express fixed, range, or variable compensation. The provider preserves the source token and exact decimal strings:

```json
{
  "compensation": {
    "type": "fixed",
    "token": "USDC",
    "advertised_amount": "500",
    "min_ask": null,
    "max_ask": null,
    "prize_breakdown": {"1": "250", "2": "100"},
    "competitive": true,
    "guaranteed": false,
    "usd_equivalent_inferred": false
  }
}
```

No token-to-USD conversion is performed. A prize pool, advertised project amount, or quote range is not acceptance, an award, settlement, or cash revenue.

Bounty/hackathon rows are marked `competitive=true`; every row remains `guaranteed=false` until some separate authoritative acceptance/settlement path proves otherwise.

## Authority boundary

Every feed receipt makes the non-authority explicit:

```json
{
  "authority": {
    "discovery": "first_party_agent_feed",
    "agent_eligibility": "provider_enforced_and_revalidated",
    "sponsor_verification": "provider_enforced_and_revalidated",
    "dispatch": false,
    "submission": false,
    "payout": false,
    "cash_claim": false,
    "currency_conversion": false
  }
}
```

This module therefore does **not**:

- create or register a Superteam agent;
- submit or edit entries;
- post comments or contact sponsors;
- perform OAuth, wallet signing, trading, spend, or KYC;
- claim an agent or payout;
- assert that advertised rewards were earned or paid;
- feed non-USD token amounts into the existing USD-only expected-value ranker.

A later provider-specific live-intake gate can consume this normalized source and establish dispatch authority without weakening the existing GitHub/RustChain canonical-source contract.

## Failure behavior

The provider fails closed on malformed eligibility, sponsor verification, status, compensation bounds, exact decimals, duplicate pagination, invalid response shape, oversized responses, redirects, authentication failure, and rate limiting. Upstream error bodies are not copied into local errors.

The focused regression suite is `tests/test_superteam_provider.py`.
