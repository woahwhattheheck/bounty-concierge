# Algora first-party canonical admission

Use this standalone preflight before deciding whether an Algora marketplace row is worth source work. It never creates a provider claim, implementation lease, PR, award or payout.

Command: python -m concierge.algora_canonical_probe listing.json

Example listing.json:

    {
      "listing_url": "https://algora.io/projectdiscovery/bounties",
      "canonical_issue_url": "https://github.com/projectdiscovery/nuclei/issues/6674",
      "listing_state": "OPEN",
      "advertised_usd": "100"
    }

First-party checks: canonical repository identity, fork and archive state; exact GitHub issue state; assignees; bounded open-PR reference census (maximum four pages, 100 PRs per page). GitHub 403/429/timeout yields HOLD with provider status/rate headers; no retry storm. Closed issue, archived/fork sponsor, or closed marketplace row PRUNEs before spending PR-census reads.

Clean live evidence results in REVIEW rather than automatic dispatch. A PR that does not explicitly reference the issue may evade a text census, and Algora market amounts alone cannot prove assignment, entitlement, award or received cash. Existing bounty_canonical_viability and MOVA lease gates remain required. Advertised USD is separate from verified cash fields (which remain unknown/null), and all claim/build/publish/payment authority bits remain false.

Optionally set GITHUB_TOKEN in your existing authorized environment. Its value is never displayed. One request per repo and one per issue, then only bounded PR pages when necessary; no background polling. Prefer a single operator owning a repo census to avoid multiple agents repeating expensive checks.

Focused test: python -m unittest tests.test_algora_canonical_probe

Regression fixtures: Algora OPEN while GitHub CLOSED, sponsor archived or forked, overlapping open PR, incomplete PR pagination, missing first-party read, and nonlive reader admission. These fixtures are offline; for live proof, compare the exact GitHub provider receipt and the canonical target at execution time.

Built in response to the October 8, 2026 first-party drift discoveries: projectdiscovery/nuclei #6674/#6532 and archived tscircuit/autorouting #92. Those marketplace rows are not booked revenue.
