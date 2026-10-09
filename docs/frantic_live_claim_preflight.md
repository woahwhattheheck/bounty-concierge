# Live Frantic bounty admission

Before assigning *new* Frantic work, perform a read-only, live candidate check:

```sh
python -m concierge.frantic_claim_preflight 120
```

This reads the documented [Frantic first-party public API](https://gofrantic.com/openapi.json), `GET https://gofrantic.com/v1/bounties/{id}`, **once per invocation** without a credential, redirect, retry or GitHub API fan-out. It is intentionally distinct from the offline policy/economics guard in Commons.

The JSON reports:

- `price_usd`: the **per-worker advertised price** from exact first-party `price_cents`, subject to the owner's **$15 minimum**.
- `vendor_fee_usd`: the **poster/vendor-side platform fee** from `fee_cents`, never assumed to be deducted from a worker's price.
- `funded`, `capacity`, `occupied_slots`, `available_slots`: first-party live claim capacity, checked for internal reconciliation.
- `claim_gate_state` and `claim_gate_available`: Frantic's public action readiness, distinct from a particular operator's account eligibility.
- `observed_at`, `source_url`, `observation_sha256`, `reason_codes`: observation metadata for debugging. The SHA-256 is unkeyed self-integrity, **not provider authentication or an eternal admission grant**.

`HOLD_*` (CLI exit 1) means **no new build/claim** on the supplied evidence. Below-floor rewards, zero slots, missing funds, closed actions, missing operator identity, invalid payloads and HTTP errors fail closed. A clean result is `READY_FOR_OPERATOR_RECHECK` (CLI exit 0), not an approval to start code or claim: operator-specific eligibility, sponsor historical paid-merge proof, maintainer recency, source requirements, conflicting PRs, payout rails and a fresh source check still apply at the time of action. Saved receipts cannot authorize future work.

The current Sourcey/Frantic #120 mismatch motivated this guard: upstream Sourcey has open vendor-record issues, but [Frantic's bounty 120 listing](https://gofrantic.com/bounties/120) reports a $1 worker price, a separate $15 poster fee and zero free claim slots. Existing delivered claims and original contributors' compensation rights are unaffected; this guard is for **new** work only.

The CLI needs Python 3 and `requests` (already used elsewhere in this repository). All network errors return a hold result, never silently treat a stale marketplace card as live. The public endpoint can restrict anonymous detail; unavailable authenticated-only evidence requires an operator check rather than guessing.
