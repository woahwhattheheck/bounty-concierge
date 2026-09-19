# GrantFox current-source baseline — Gryd-lock/grydlock-testkit #9

Operation: `GFOX2-20260919-058-R-ZZ-SOLSTICE`  
Worker: ZZ-Solstice · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `7064404d6e7c44df1f980d532d23901651d79426`

## Current provider / issue state

- GitHub issue #9 is OPEN and unassigned.
- GrantFox listing was observed live as **Unassigned** with **Apply to this issue** available.
- Two earlier contributor application comments were present.
- No matching open implementation PR was found during the baseline pass.
- Reward/campaign labels do not establish an award or payment.

Per the provider contributor guide, implementation remains assignment-dependent. This packet stops at current-source analysis and prepared application text.

## Exact remaining gap

- `scores.json` blob `f27d17c781a9059cc1304af93def554391b63876` contains 12 current score entries.
- `CHANGELOG.md` blob `2eddb818a16f7483ed5a83b3d9949b17fd978d62` has an existing populated `[Unreleased]` section.
- `.github/workflows/ci.yml` blob `53ce3ea3359798fd8b34d3a06728ae8110f7277c` only verifies that *some* unreleased changelog content exists whenever fixture files change. It does not bind an existing score change to an updated score snapshot or prove that the changelog content describes that score change.
- There is no checked-in score snapshot on this observed baseline.

That means an unrelated pre-existing `[Unreleased]` entry can satisfy the current changelog job even if an existing destination's score changes silently.

## Bounded implementation after assignment

1. Refresh main, linked PRs, and provider assignment.
2. Add a checked-in canonical score snapshot.
3. Add a deterministic comparator that distinguishes existing-destination score changes from brand-new destination additions.
4. Fail with the exact changed destination IDs and old/new values when an existing score changes without deliberate snapshot + changelog movement.
5. Allow brand-new destinations without forcing snapshot ceremony for unrelated historical rows.
6. Ensure a changed existing score passes only when the snapshot changes coherently and current-PR changelog intent is present; do not accept arbitrary stale `[Unreleased]` text as sufficient intent.
7. Wire the guard beside existing validation without weakening secret, fixture, or changelog checks.

## Prepared application text

> I’d like to take this on. I inspected current main at 7064404d6e7c44df1f980d532d23901651d79426: scores.json has 12 entries and CI’s current changelog check only requires some [Unreleased] content whenever fixture files change; it does not prove an existing score change was paired with an intentional snapshot update and a relevant changelog entry. I’d add a checked-in score snapshot plus a deterministic CI comparator that reports exactly changed existing destinations, allows brand-new destinations without ceremony, and only accepts changed existing scores when both snapshot and current change intent move together. I’ll keep this isolated from scoring semantics and preserve existing validation. Please assign me through GrantFox if this approach fits.

Application status in this packet: **prepared, not submitted**. Record submission only from a concrete provider/GitHub receipt.

No upstream implementation, assignment, wallet, award, payment, or revenue authority is created by this baseline.
