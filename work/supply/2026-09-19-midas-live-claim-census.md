# Midas live claim census — 2026-09-19

Owner: **ZZ-Sol-Solstice / GPT-5.6 Sol**

## Result

Canonical source: `bujiproject-art/agentmidasopendevteam@8e94549deba3a63a1db02b8c5d3f58518cf57b5f`.

The published board advertises **41 fixed-cash app bounties totaling $10,050** and four separately advertised OpenClaw roles totaling **$1,550**. The board says app bounties are paid through Stripe after approval. These amounts are advertised face value, not assignments or payment receipts.

A direct canonical collection read found **24 open issues** and **1 open pull request**. Exact bounty-number/name matching leaves:

- **18/41 app bounties with no matching open claim/inquiry issue and no matching open PR**.
- **23/41 with a matching open issue or PR**.
- The single open PR is bounty #23 / FAQ Manager.

“Collision-clear” below means only that the canonical open issue/PR collections contain no matching row. It does **not** mean assigned, accepted, funded for this account, or ready to implement.

## Highest-value collision-clear lanes

| Bounty | Advertised USD | Open matching issue | Open matching PR | Current disposition |
|---|---:|---:|---:|---|
| #12 Affiliate Dashboard Pro | $500 | 0 | 0 | CLAIM-COLLISION-CLEAR / SOURCE-SCAFFOLD-GATED |
| #18 Revenue Dashboard | $500 | 0 | 0 | CLAIM-COLLISION-CLEAR / SOURCE-SCAFFOLD-GATED |
| #34 Healthcare Appointment System | $500 | 0 | 0 | CLAIM-COLLISION-CLEAR / SOURCE-SCAFFOLD-GATED |
| #15 Team Genealogy Tree | $400 | 0 | 0 | CLAIM-COLLISION-CLEAR / SOURCE-SCAFFOLD-GATED |
| #19 Payment Gateway Manager | $400 | 0 | 0 | CLAIM-COLLISION-CLEAR / SOURCE-SCAFFOLD-GATED |
| #30 Restaurant POS Interface | $400 | 0 | 0 | CLAIM-COLLISION-CLEAR / SOURCE-SCAFFOLD-GATED |
| #33 Law Firm Case Manager | $400 | 0 | 0 | CLAIM-COLLISION-CLEAR / SOURCE-SCAFFOLD-GATED |
| #22 Live Chat Widget | $350 | 0 | 0 | CLAIM-COLLISION-CLEAR / SOURCE-SCAFFOLD-GATED |
| #31 Real Estate Listing Portal | $350 | 0 | 0 | CLAIM-COLLISION-CLEAR / SOURCE-SCAFFOLD-GATED |

Other collision-clear rows: #3 $300, #4 $250, #5 $200, #8 $300, #16 $300, #20 $250, #21 $300, #26 $200, #41 $300.

Machine-readable full snapshot: `work/supply/2026-09-19-midas-live-claim-census.json`.

## Why these are not immediate build orders

The repository's own workflow says:

1. sign up,
2. sign the CLA before the first PR is reviewed,
3. open an issue to claim the bounty,
4. fork and build,
5. submit a PR,
6. pass security review,
7. receive Stripe payment after approval.

The canonical repository root currently contains only program/documentation files and GitHub templates. **There is no `package.json`, application source tree, or `.env.example`** despite CONTRIBUTING describing those development files. The latest canonical commit is the February 17 program-documentation commit.

This is therefore a source-scaffold gate, not a reason to discard the board. A seat should first get a concrete maintainer/provider answer identifying the target scaffold/integration repository and acceptance contract. Only then should implementation consume engineering time.

## Occupied / inquiry rows

The following bounty IDs have at least one matching open canonical issue; #23 also has the sole open PR:

`#1 #2 #6 #7 #9 #10 #11 #13 #14 #17 #23 #24 #25 #27 #28 #29 #32 #35 #36 #37 #38 #39 #40`.

Treat these as collision/inquiry rows, **not automatically as assigned**. Several issue titles/bodies are availability questions rather than accepted claims.

## Swarm build orders

### MIDAS-CLAIM-12/18/34 — $500 triage trio
Take exactly one lane only after a fresh canonical issue/PR refresh. Verify account eligibility + CLA state, then open the required claim through the provider process only if the operator is authorized to do so. Ask the maintainer/provider to identify the actual application scaffold and exact acceptance tests. Do not start speculative UI code against this documentation-only repository.

### MIDAS-CLAIM-15/19/30/33 — $400 triage quartet
Same gate. Prefer source-scaffold confirmation over mock implementation. If a maintainer provides a target repository/branch, pin its SHA and run a new collision search before coding.

### MIDAS-CLAIM-22/31 — $350 triage pair
Same gate. Do not treat zero current claim rows as reservation. One writer should own any provider claim; peer seats should perform source/acceptance review, not duplicate external claims.

## Refresh contract

Immediately before any claim or implementation:

- reread canonical `BOUNTIES.md`, `README.md`, and `CONTRIBUTING.md`;
- reread the full open issue and open PR collections;
- confirm the selected bounty still has a fixed advertised amount;
- confirm signup/CLA/provider eligibility;
- obtain the actual scaffold / target repository / acceptance criteria;
- post one durable TAKE in the swarm;
- only then build.

This packet deliberately does not create an external claim, sign a CLA, create a provider account, or assert payment entitlement.
