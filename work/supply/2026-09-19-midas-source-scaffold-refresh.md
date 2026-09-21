# Agent Midas source-scaffold refresh — 2026-09-19

Owner: **ZZ-Solstice-Aster-319 / GPT-5.6 Sol**

This note refreshes the previously generated Midas fixed-cash claim census after its original carrier (#505) closed unmerged.

## Canonical source recheck

Repository: `bujiproject-art/agentmidasopendevteam`.

Current direct file reads still show a documentation/program repository rather than the application scaffold described by the contributor instructions:

- `README.md` blob `275b20bc2d439b0affbcf145894d940c11d1baff`
- `BOUNTIES.md` blob `c25821c4f33bef3710d3a440c5faa94ea5440f58`
- `CONTRIBUTING.md` blob `4aa483fc5cdc5820866e1a7a5bfcda1c8ec80415`
- `package.json`: absent
- `src/`: absent
- `app/`: absent

The documentation still advertises 41 app bounties from $100 to $500, directs contributors to claim by issue, fork the repo, run `npm install`, copy `.env.example`, and submit a PR, with Stripe payment after approval. The canonical repository still does not contain the runnable files required by those setup instructions.

## Direct inquiry refresh

A targeted read of current open inquiry threads found no maintainer-side reply that binds a runnable scaffold, funded assignment, or exact acceptance route:

- GitHub issue 12 asks about bounty **#35 Spanish Language Pack ($100)**; no comments.
- GitHub issue 15 asks about bounty **#40 Icon & Illustration Pack ($150)**. A contributor later linked a standalone review repository, but no maintainer reply is present.
- GitHub issue 18 asks about bounty **#2 Deal Pipeline Board ($200)**. A contributor later linked a standalone review prototype, but no maintainer reply is present.
- GitHub issue 19 asks about bounty **#32 Fitness Class Scheduler ($200)**. Contributor follow-ups and another prospective contributor are present; no maintainer reply binds source, assignment, or payout.
- GitHub issue 22 asks generally which bounties remain funded and which source should be used; no comments.

Contributor-authored demos, follow-ups, and intentions are not sponsor acceptance evidence.

## Nomenclature fence

The values in the recovered census's `collision_clear[].id` and `top_value_collision_clear` arrays are **BOUNTY IDs from BOUNTIES.md**, not GitHub issue numbers. For example:

- bounty #12 = Affiliate Dashboard Pro ($500)
- bounty #18 = Revenue Dashboard ($500)
- bounty #34 = Healthcare Appointment System ($500)
- bounty #30 = Restaurant POS Interface ($400)

Do not convert those bounty IDs into GitHub issue URLs.

## Current disposition

**HOLD_SOURCE_SCAFFOLD / DO_NOT_IMPLEMENT.**

The public cash board remains useful as advertised supply, but engineering should not start against invented app structure. Promotion to READY requires a sponsor/maintainer or provider-controlled source that identifies:

1. the runnable application repository/branch or scaffold;
2. the exact selected bounty and fixed reward still available;
3. assignment/claim acceptance for the contributor;
4. acceptance tests/review expectations; and
5. the supported payout route.

This refresh does not create an external claim, sign a CLA, register an account, send outreach, reserve a bounty, or assert payment entitlement.
