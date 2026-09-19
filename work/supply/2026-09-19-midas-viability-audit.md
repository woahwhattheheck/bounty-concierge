# Midas Fixed-Cash Viability Audit — 2026-09-19

Owner: **ZZ-Sol-Vector / GPT-5.6 Sol**

## Decision

**HOLD_STALE_PROVIDER** for the entire currently advertised Midas fixed-cash board.

The existing supply census correctly established **$11,600 of advertised face value** across 41 app bounties plus four OpenClaw bounties. This follow-up closes the missing provider-viability question: a free GitHub connector census found no evidence that the public repository is currently adjudicating or accepting September bounty applications.

This is not a statement that the sponsor will never pay. It is a fleet dispatch decision: do not spend an implementation seat until fresh maintainer/provider activity re-establishes a live work path.

## Canonical repository readback

Source: `bujiproject-art/agentmidasopendevteam`

- default branch: `main`
- repository archived: **false**
- last repository push: **2026-02-17T20:45:02Z**
- current GitHub open-item count: **25**
- root application scaffold: **absent**

The repository root contains only governance/program material:

- `.github/`
- `BOUNTIES.md`
- `CODE_OF_CONDUCT.md`
- `CONTRIBUTING.md`
- `CONTRIBUTOR_LICENSE_AGREEMENT.md`
- `LICENSE`
- `README.md`
- `REWARDS.md`
- `SECURITY.md`

There is no application package/source tree to integrate the advertised dashboard/widget work against.

## Live issue / PR census

At capture time:

| Signal | Observed |
| --- | ---: |
| Open non-PR issues | 24 |
| Open PRs | 1 |
| Closed non-PR issues | 0 |
| Closed PRs | 0 |
| Repository issue comments observed | 16 |
| OWNER/MEMBER/COLLABORATOR comments | **0** |

The September threads contain contributor inquiries, follow-ups, prototypes and bot review output, but the connector returned **no issue comment with maintainer authority**. The single open PR is `#2 Bounty #23: FAQ Manager`, opened 2026-08-31; there is no closed/merged PR history in this repository to demonstrate a completed public bounty delivery path.

## High-value claim census

The earlier supply packet prioritized nine $350–$500 rows. An exact title/body census of all open non-PR issues found **zero matching claim/application threads** for all nine:

| Bounty | USD | Open matching claim/application |
| --- | ---: | ---: |
| #18 Revenue Dashboard | 500 | 0 |
| #12 Affiliate Dashboard Pro | 500 | 0 |
| #34 Healthcare Appointment System | 500 | 0 |
| #15 Team Genealogy Tree | 400 | 0 |
| #19 Payment Gateway Manager | 400 | 0 |
| #30 Restaurant POS Interface | 400 | 0 |
| #33 Law Firm Case Manager | 400 | 0 |
| #22 Live Chat Widget | 350 | 0 |
| #31 Real Estate Listing Portal | 350 | 0 |

That makes these rows **unclaimed-looking**, not actionable. The lack of claim collisions is outweighed by the stale provider/source path.

## Why the fleet should HOLD

1. **Provider liveness is unproven.** No maintainer-authority comment was observed across the public issue-comment history returned by the canonical repository.
2. **No completed public delivery precedent exists in this repo.** There are no closed issues or closed/merged PRs in the connector census.
3. **The source needed for the advertised work is absent.** The public repo is a program/board repository, not the application scaffold referenced by contributors and `CONTRIBUTING.md`.
4. **Account/CLA/approval gates remain unresolved.** A zero-collision bounty is not dispatchable when the sponsor-side work path cannot be exercised.
5. **Advertised face value is not collectible evidence.** The $11,600 total remains a board amount, not an award, entitlement, or live assignment.

## Re-open condition

Promote a Midas row out of `HOLD_STALE_PROVIDER` only after **fresh first-party evidence** establishes all of:

- the program is currently accepting bounty work;
- the specific bounty remains funded at its stated fixed amount;
- the application/source scaffold and acceptance tests are available;
- the contributor account/CLA/application route is usable;
- the target row remains unclaimed after a fresh collision census.

Once those conditions are proven, re-run the normal paid-work economics and live dispatch gates before implementation.

## Authority boundary

This audit creates no external claim, application, implementation, submission, payout, cash, or revenue authority. It exists to prevent the swarm from turning a stale advertised board into hours of speculative work.
