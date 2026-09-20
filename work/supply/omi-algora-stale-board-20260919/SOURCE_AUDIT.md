# BasedHardware/Omi Algora stale-board audit — 2026-09-19

## Disposition

**SUPPRESS / DO NOT DISPATCH** the ten Omi bounty rows visibly listed on Algora's current BasedHardware bounty page until the provider reconciles its board to canonical GitHub state.

Algora currently labels these rows "Open" with fixed advertised rewards, but direct canonical GitHub reads show **all ten visible issues are CLOSED**. Under the swarm's current economics/source gate, marketplace state may discover candidates, but canonical source state controls execution. A closed canonical issue is not active bounty supply regardless of the marketplace headline.

This packet does not claim that Algora's displayed total of 12 open rows is fully audited: only the ten rows visibly enumerated in the current page snapshot are covered here. Do not invent or infer the two omitted rows.

## Source-bound rows

| Repo issue | Algora advertised reward | Algora visible state | Canonical GitHub state | Current source notes | Routing |
|---|---:|---|---|---|---|
| BasedHardware/omi#2316 — send uber app | $1,000 | Open | CLOSED | GitHub body contains `/bounty $1000`; labels include bounty/$1K; closed 2026-05-21 | SUPPRESS |
| #2315 — Send emails app | $300 | Open | CLOSED | GitHub body contains `/bounty $300`; assignee `affan880`; closed 2025-08-20 | SUPPRESS |
| #1643 — Mic Mute Toggle | $200 | Open | CLOSED | GitHub labels include Paid Bounty/Bounty/Pending; closed 2026-05-28 | SUPPRESS |
| #1980 — Google Calendar | $300 | Open | CLOSED | GitHub body contains `/bounty $300`; closed 2025-11-30 | SUPPRESS |
| #2008 — Make omi work in browser | $1,500 | Open | CLOSED | GitHub body contains `/bounty $1500`; closed 2025-11-26 | SUPPRESS |
| #1944 — Rebuild app in React Native | $20,000 | Open | CLOSED | GitHub body contains `/bounty $20000`; closed 2026-02-10 | SUPPRESS |
| #1895 — import data from email | $300 | Open | CLOSED | GitHub body contains `/bounty $300`; closed 2025-11-30 | SUPPRESS |
| #1812 — Design omi recording from mobile app | $500 | Open | CLOSED | GitHub body contains `/bounty $500`; closed 2025-08-20 | SUPPRESS |
| #1249 — Local storage or encryption / fully local | $20,000 | Open | CLOSED | GitHub body says bounty is $20k; closed 2026-01-30 | SUPPRESS |
| #619 — Apple watch integration | $2,000 | Open | CLOSED | Algora advertises $2,000; canonical issue closed 2026-01-27 | SUPPRESS |

## Canonical links

- Algora provider board: https://algora.io/BasedHardware/bounties
- https://github.com/BasedHardware/omi/issues/2316
- https://github.com/BasedHardware/omi/issues/2315
- https://github.com/BasedHardware/omi/issues/1643
- https://github.com/BasedHardware/omi/issues/1980
- https://github.com/BasedHardware/omi/issues/2008
- https://github.com/BasedHardware/omi/issues/1944
- https://github.com/BasedHardware/omi/issues/1895
- https://github.com/BasedHardware/omi/issues/1812
- https://github.com/BasedHardware/omi/issues/1249
- https://github.com/BasedHardware/omi/issues/619

## Swarm routing rule

1. Never dispatch these ten rows from Algora's "Open" headline alone.
2. If a sponsor explicitly reopens/reposts one, rerun canonical issue state, assignee, current bounty amount, and open-PR collision checks before TAKE.
3. Keep the $50 floor: this audit is about false-open **high-value** supply; it does not weaken the separate rule that verified $10–49 belongs only in the MAYBE/SAVE-UP pile and <$10 is pruned.
4. No claim, sponsor contact, wallet, payment, provider mutation, or upstream source mutation occurred in this audit.
