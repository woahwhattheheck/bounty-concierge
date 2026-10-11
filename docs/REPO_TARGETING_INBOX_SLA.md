# Repo targeting, dead-repo pruning and inbox SLA

## Bounty trust ban — owner directive, 2026-10-11

**BOUNTY-TRUST-BAN-20261011 is current and overrides contradictory bounty instructions below.** Bounty work is banned by default unless the maintainer is reputable and proven legitimate on retained evidence. Continued work is permitted for established real repositories with substantial stars where Bryce's contributions have already merged, and other evidence-backed proven maintainers. Stars alone, platform presence, advertised rewards, agent claims and old blanket GREEN lists do not establish this exception. Mova is expressly excluded.

Stop all excluded bounty discovery/qualification for work, claiming, implementation, reviews, submissions, follow-ups, payment collection, demands, queued sends, retries and watchers. Unknown/unproven maintainers are excluded. Do not resume through another model, account, carrier or a generic keep-working instruction. This later owner rule overrides claim-preservation instructions to continue collections on excluded matters: retain historical claims, source, submissions and evidence, but make no external collection or waiver.

Use the exact repository/maintainer evidence recorded in the canonical registry; do not invent a numerical star threshold or treat a fork, our PR authorship, third-party merged work or a claimed reward as proof. Check the current policy before dispatch and carry it into current, future, delegated and resumed workers. Suppress excluded pending operations in the actual queue and record their IDs and read-back disposition. Publishing these source rules alone is not evidence that a live queue or deployed publisher has adopted them.

Non-bounty SCF/customer/active-competition work continues. Existing contact holds, account routing, provider limits and focused-validation/cost rules remain in force. Preserve evidence for authorized factual reports and blocks; lack of trust evidence does not establish fraud or malware.

Canonical policy: https://github.com/woahwhattheheck/commons/blob/main/ground/BOUNTY_TRUST_BAN.md
Canonical registry: https://github.com/woahwhattheheck/commons/blob/main/docs/policies/maintainer-credibility-registry.json
Owner directive: https://tokenjunkielabs.slack.com/archives/C0BU51F1PL3/p1791743946412259


Owner direction FLEET-COMMON-SENSE-20261009-BRYCE-02. Policy file: `policies/repo_targeting_v1.json` (schema `repo-targeting-policy/v1`). This sits after the 2026-10-08 eligibility gate (active maintainer + documented paid merge, REPO-ELIGIBILITY-20261009-BRYCE-01) and answers a different question: **of the repos we may work in, where does effort go, where does it stop, and who answers the inbox.**

## Why

Measured 2026-10-09 (UTC), outside-repo PRs by @woahwhattheheck:

| window | opened | merged | note |
|---|---|---|---|
| September | 243 | 38 (16%) | merges concentrated: mova-store 19, Quittance0 10, bottube 8 |
| Oct 5–8 | 666 | 16 in all of October (10 on Oct 3) | 49 repos; 61 PRs into one repo on Oct 8, 53 into another |

785 outside PRs were open. github.com/notifications held 4,923 unread: 4,508 were self-notifications from our own repositories, ~415 external, 183 older than three days, oldest Sept 7. No seat owned the inbox; seats reported stopping all GitHub reads after search fan-out 403s on Oct 5, 6 and 8.

## Rules

1. **Effort follows merges.** Priority within eligible repos = our merged PRs in the last `priority.merged_lookback_days` (60) plus documented paid merges. New work and inbox attention go there first. No new source batches into a repo where nobody's PR has merged in 30 days.
2. **Per-repo volume cap.** At most `volume_cap.max_open_unreviewed_prs_per_repo` (5) open un-reviewed PRs of ours in one external repo, and no more new PRs per repo per day than `max(2, our merges there in the last 30 days)`. HOLD `REPO_VOLUME_CAP`.
3. **Dead repos are pruned.** DEAD = no PR from anyone merged in 30 days AND no maintainer review/comment in 14 days, OR payment rail confirmed not integrated / unfunded with no creator response in 14 days. HOLD `REPO_DEAD`: no new TAKEs, no claim posts, no repairing other contributors' PRs there, delete our fork when it carries no open PR of ours. Existing open PRs stay open and untouched with authors, claim IDs and payment linkage preserved; closing one is the owner's call only. `known_dead` lists repos already classified with evidence.
4. **Inbox SLA, one owner.** One named seat owns the notifications inbox for both accounts continuously and posts its TAKE in #github-inbox. Every external notification is read and actioned within 24 hours; actioned means the real thing (rebase pushed, question answered, review fixed) or marked done when nothing is owed. Unactioned items past 24 hours are HOLD `INBOX_SLA_BREACH` and outrank new TAKEs for that seat. The 2026-10-09 backlog clears within 72 hours, oldest first, priority repos first.
5. **Inbox hygiene.** Our own repositories' notifications are auto-marked done / unwatched so external signal is visible. Daily digest to #github-inbox: external unread by repo and age, oldest unanswered items.
6. **Rate budget.** Read the inbox through `GET /notifications` with `If-Modified-Since`, honoring `X-Poll-Interval`; 304 responses do not count against the rate limit. Never through search.
7. **Reply content** on external surfaces follows the 2026-09-07 NO PUBLIC L rule.

## What this does not do

It does not establish eligibility, funding, acceptance or payment; those stay with the existing gates. It does not authorize closing, editing or withdrawing any existing PR, and it does not change account routing (original authors and claim identities stay as they are).
