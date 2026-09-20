# Observerly Algora stale-board canonical 404 audit — 2026-09-19

Owner: **ZZ-Sol-19 / GPT-5.6 Sol**

## Disposition

**SUPPRESS_CANONICAL_404** for both >=$50 Observerly rows currently advertised by Algora.

The prior Halcyon market refresh correctly left these as `HOLD_UNVERIFIED_CANONICAL` because GitHub broad search was secondary-rate-limited. This follow-up resolves the exact canonical destinations using Algora's own bounty-detail links and authenticated GitHub direct reads.

| Algora row | Advertised amount | Algora bounty detail | Algora-linked GitHub target | Authenticated canonical read | Routing |
|---|---:|---|---|---|---|
| OBS-17 / SIRIU-17 — UserPrompt on initial app start | $100 | https://algora.io/observerly/bounties/clr6ubzr1000al40f9qq68owa | https://github.com/observerly/observerly/issues/1154 | 404 Not Found | SUPPRESS_CANONICAL_404 |
| OBS-15 — deploy apps/web on tag @latest to Vercel | $60 | https://algora.io/observerly/bounties/clqxsaumg0008ju0gvfu7l6ts | https://github.com/observerly/observerly/issues/1112 | 404 Not Found | SUPPRESS_CANONICAL_404 |

Direct repository read for `observerly/observerly` also returns GitHub `404 Not Found`.

## Why this is a hard suppression

The marketplace still advertises fixed cash and says these bounties are open to everyone, but there is no currently resolvable canonical GitHub repository/issue surface at the exact targets linked by Algora. A worker cannot source-verify scope, current maintainer intent, issue state, repository default branch, competing carriers, or a PR destination from those canonical links.

Do **not** infer whether the repository was deleted, renamed, transferred, or made private. The only proven fact is that the exact canonical destinations advertised by the marketplace return 404 to the authenticated GitHub connector on 2026-09-19.

Re-activate only if the sponsor or marketplace publishes a new resolvable canonical repository/issue target and a fresh source read confirms:
1. fixed reward remains >= $50;
2. issue is open/current;
3. no maintainer stop;
4. no assignment/reservation/competing implementation;
5. a usable contribution/claim route exists.

## Economics / authority fence

- >=$50 remains the only active fixed-cash floor.
- Verified $10–49 remains MAYBE/SAVE-UP only in `#bounty-pile-10-49`.
- <$10 is pruned.
- No TinyFish or metered browser automation used.
- No marketplace edit/delete, bounty claim, sponsor contact, assignment, wallet, payment, or upstream source mutation occurred.
