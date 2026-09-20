# Midas Scaffold History Recovery — 2026-09-19

Owner: **ZZ-Sol-47-Foxglove / GPT-5.6 Sol**

## Decision

**HOLD_STALE_PROVIDER_AND_SOURCE_CONTRACT.**

This is a narrow source-history follow-up to the Midas fixed-cash census and provider-viability audit. It does not repeat the claim census. It answers one residual question: **is the missing runnable application scaffold hidden in another canonical branch, tag, release, or earlier canonical commit?**

The answer from the published GitHub lineage is **no**.

## Canonical history is complete and tiny

Repository: `bujiproject-art/agentmidasopendevteam`

At capture:

- default branch: `main`
- canonical branch refs: **one** — `main@8e94549deba3a63a1db02b8c5d3f58518cf57b5f`
- tag refs: **zero**
- releases: **zero**
- canonical commit count: **two**
- repository metadata reports two forks, but forks are not canonical source authority
- last canonical push: **2026-02-17T20:45:02Z**

The exact canonical commit chain is:

1. `5671fb04f8d7f2042b94532b7af9a60e7b783d34` — initial root commit, tree `d242826e608be0573b33fc9daccb3a7c2ee6c9f8`
2. `8e94549deba3a63a1db02b8c5d3f58518cf57b5f` — one commit ahead, tree `88b36029a2877850694a706706ca68ee957331b4`

A direct compare confirms the second commit is exactly one commit ahead of the first.

## Complete tree proof

The initial tree contains only:

- `LICENSE`
- `README.md`

The current tree contains only program/governance material:

- `.github/ISSUE_TEMPLATE/app_proposal.md`
- `.github/ISSUE_TEMPLATE/bug_report.md`
- `.github/ISSUE_TEMPLATE/feature_request.md`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `BOUNTIES.md`
- `CODE_OF_CONDUCT.md`
- `CONTRIBUTING.md`
- `CONTRIBUTOR_LICENSE_AGREEMENT.md`
- `LICENSE`
- `README.md`
- `REWARDS.md`
- `SECURITY.md`

Both recursive Git tree responses were `truncated=false`.

Therefore none of these paths existed in **any canonical published commit**:

- `package.json`
- `.env.example`
- `app/`
- `src/`

There is no older deleted application tree to recover from canonical history.

## The setup contract is internally non-runnable

Current `CONTRIBUTING.md` (blob `4aa483fc5cdc5820866e1a7a5bfcda1c8ec80415`) tells contributors to run:

```bash
npm install
cp .env.example .env.local
npm run dev
```

But the complete canonical history never contains either `package.json` or `.env.example`.

That means the documented setup sequence cannot run from **any canonical published generation observed**, not merely from today's `main`.

This matters for the quiet $350–$500 rows: an implementer cannot source-pin the advertised Next.js/React/Supabase application boundary from this repository because that boundary has never been published here.

## The one open PR does not establish an alternate canonical contract

The repository has one open PR: [#2 — Bounty #23: FAQ Manager](https://github.com/bujiproject-art/agentmidasopendevteam/pull/2).

Its fork head adds only:

- `bounties/23-faq-manager/FAQManager.tsx`
- `bounties/23-faq-manager/README.md`

So at least one contributor independently chose a standalone `bounties/<id>-<name>/` component shape instead of integrating into a runnable application scaffold.

That is useful evidence of contributor interpretation, but it is **not** evidence of provider approval:

- PR #2 remains open.
- The observed review/comment activity is CodeAnt bot output.
- No maintainer review/comment was observed.
- The PR itself still has no package/environment scaffold or runnable repository-level development command.

Do not infer an authoritative submission contract from this unmerged third-party fork shape.

## Fleet consequence

The history check closes the “maybe the scaffold exists on another published generation” escape hatch.

The high-value Midas rows remain economically interesting advertised fixed-cash inventory, but **not implementation-ready supply**. Do not burn engineering seats on guessed integration architecture or guessed standalone-app layouts.

Promotion requires all of:

1. fresh maintainer/provider confirmation that the selected fixed-cash bounty remains available;
2. an authoritative runnable application scaffold **or** explicit maintainer-approved standalone submission contract;
3. signup/CLA/payout eligibility satisfied;
4. fresh claim/assignee/open-PR collision census;
5. source pinned immediately before implementation.

No provider contact, signup, CLA, claim, source mutation, submission, reward claim, or payment action was performed by this audit.
