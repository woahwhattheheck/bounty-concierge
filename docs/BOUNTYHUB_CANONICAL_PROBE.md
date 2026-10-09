# BountyHub canonical source check

Read source-of-truth GitHub issue state before allocating engineering to a
funded BountyHub listing. The BountyHub catalog is discovery evidence, not
proof that a GitHub repo or issue still exists. On October 9, 2026 multiple
Fluxer-meta listings returned canonical GitHub HTTP404 and an AzerothCore
listing pointed to an already-closed issue.

First use concierge.bountyhub_catalog.select_targets() to produce a **funded
shortlist** JSON. Then run:

    python -m concierge.bountyhub_canonical_probe shortlist.json --max-requests 5

A previously authenticated GITHUB_TOKEN may be supplied via secure runtime
environment; none is printed. A bounded default of 5 GETs, 0..100 permitted,
each canonical issue only once, no redirects and no retries. HTTP403/429 and
transport errors end the attempt without burning the remaining budget.

- PROCEED_TO_PREFLIGHT: live OPEN GitHub issue, but NOT authorization to
  implement, submit a claim or presume money. Maintainer, paid-merge payer
  precedent, collision, submission, ownership and funding still need checks.
- PRUNE_NEW_BUILD: GitHub original is CLOSED. Existing original author PRs,
  claims and possible payments remain intact.
- HOLD: 404/410 missing OR inaccessible (not definitive deletion), move,
  quota, permission, invalid payload or unchecked due budget.

Read-only outputs include source issue identity, status/HTTP code, capture UTC
and SHA-256 receipt. They never assert payment or remove existing credits.
One focused offline test fixture: python -m unittest tests.test_bountyhub_canonical_probe.
No autonomous sponsor contact or source dispatch is wired by this new tool.
