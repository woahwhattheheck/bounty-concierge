# Frantic first-party claim-window evidence

Source: Frantic's documented public, anonymous, read-only GET https://gofrantic.com/v1/bounties/{id}. The upstream OpenAPI schema declares bounty.price_cents, fee_cents, funded, claim_progress.capacity/occupied/available, and actions.claim.available/state. Use these structured fields instead of parsing search-board headlines or HTML. Do not send operator/agent credentials to a public-intake probe.

Run:
    python -m concierge.frantic_public_bounty_evidence 120 --output claim120.json

For a saved official response, replay:
    python -m concierge.frantic_public_bounty_evidence 120 --input firstparty-response.json --output claim120.json

Both are observation only; neither posts a claim, opens a PR, creates an allocation, nor verifies payment. A live network failure or inconsistent response exits 2 and must be treated as HOLD, never as permission to work.

Receipt schema frantic-public-claim-window-evidence/v1 carries the exact source URL, observation UTC time, SHA-256 of observed raw JSON, displayed worker price in listed_reward_usd, funded_usd only when funded=true, independent platform fee_usd, available_slots, claim_capacity, action state, and explicit reasons. A platform fee is NOT assumed to be deducted from a worker's price: source fields remain economically separate. The recorded funded_usd is a current listing signal, NOT a payout or settlement.

Even with open slots, candidate_for_independent_qualification=true is merely a lead. dispatch=false ALWAYS: downstream must separately check current canonical sponsor issue/PR/repo, actual same-payer prior paid merge, operator account eligibility, available authenticated claim, sponsor cap, duplicate-claim custody, fee economics under contract, and any owner hold. This avoids turning a public anonymous claim window into an executable/claim authority. Keep existing accepted/submitted work and compensation claims intact when a NEW intake is rejected.

For Sourcey-linked Frantic #120, the official page observed Oct 9 2026 showed $1 funded, $15 house fee, 0/150 slots and claim gate closed. It cannot generate a new >=$15 paid Sourcey build while closed. The repository is separate from the already-active Commons MOVA planner that accepts provider claim-window snapshots; feed this module's safe receipt to that planner after agreeing schema, without duplicating its admission logic.

One focused check:
    python -m unittest tests.test_frantic_public_bounty_evidence -v

This fixture check does not establish live Frantic availability or authenticated account eligibility.
