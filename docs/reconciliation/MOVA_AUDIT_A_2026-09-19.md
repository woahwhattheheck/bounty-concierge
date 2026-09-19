# Mova Store remaining-row audit A — 2026-09-19

Worker: **ZZ-Moraine-29 / GPT-5.6 Sol**  
Scope: read-only payout-entitlement chronology for three advertised bounty rows from the merged Mova reconciliation ledger.  
Outbound boundary: **no sponsor/GrantFox contact, no retroactive application, no issue reopen, no payment claim.**

## Result

All three rows in Audit A are **PRUNE BOUNTY CLAIM** for `@woahwhattheheck`. In each case, a different contributor's pull request was merged immediately before the bounty issue was closed, while our related PR merged later. Our merged code remains accepted related work, but the advertised issue bounty should not be counted in root's appeal/collection amount without new explicit maintainer/provider evidence.

| Issue | Advertised | Our merged PR | Actual closing carrier | Close chronology | Disposition |
|---|---:|---:|---|---|---|
| #18 | $100 | #371 | realuca660-pixel #416 | #416 merged 2026-09-08T18:31:36Z; issue closed 18:31:37Z; our #371 merged 2026-09-12T09:42:16Z | **PRUNE bounty claim** |
| #25 | $90 | #363 | ElvinGts #401 | #401 merged 2026-09-08T18:49:11Z; issue closed 18:49:12Z; our #363 merged 2026-09-12T09:43:11Z | **PRUNE bounty claim** |
| #29 | $80 | #365 | realuca660-pixel #414 | #414 merged 2026-09-08T18:38:35Z; issue closed 18:38:37Z; our #365 merged 2026-09-09T21:51:22Z | **PRUNE bounty claim** |

**Face value removed from our bounty-claim basis by this audit: $270.**

## #18 — $100 — Buffer globals → Uint8Array

Canonical issue title: `[Bounty: $100] Replace Node Buffer globals in client-side lib/stellar modules with Uint8Array conversions`.

### Closing-carrier evidence

- Issue #18 is `closed / completed` at **2026-09-08T18:31:37Z**.
- The issue event immediately preceding closure references upstream commit `4054e4b104961e59dee0e02b3c58f642c146a1b0`.
- That commit message is:
  - `fix(stellar): replace client Buffer usage with Uint8Array`
  - `Closes #18`
- GitHub maps that commit to **PR #416**, authored by `realuca660-pixel`, merged at **2026-09-08T18:31:36Z**.
- PR #416 body is exactly `Closes #18`.

### Our related work

- Our **PR #371** was opened 2026-09-06 and later merged by the maintainer at **2026-09-12T09:42:16Z**, merge `5328da53fc0912ce36e46b91ebe6b90bc3387a7e`.
- It is technically related and accepted upstream, but it landed **four days after #416 had already closed the bounty issue**.
- The issue had multiple earlier claim/submission comments (#235, #314, #379, other candidate commits), so this was not an uncontested bounty lane.
- No issue comment or PR metadata surfaced an explicit maintainer transfer of the $100 bounty to our later #371.

**Disposition:** preserve #371 as accepted related work; **do not count #18's $100 as our earned bounty** absent new explicit maintainer/provider evidence.

## #25 — $90 — Sidebar routes

Canonical issue title: `[Bounty: $90] Point Shop sidebar links at real routes or remove the dead entries`.

### Closing-carrier evidence

- Issue #25 is `closed / completed` at **2026-09-08T18:49:12Z**.
- The closing reference maps to commit `5b84909f9ea135478c0f6dba1cb8d34d617a089f`.
- That commit is the merge of **PR #401** by `ElvinGts`, merged at **2026-09-08T18:49:11Z**.
- PR #401 title: `fix(sidebar): map dead routes to valid category and collection targets (#25)`.
- PR #401 body contains both:
  - `Fixes #25`
  - `/claim #25`

### Our related work

- Our **PR #363** was opened before issue closure but merged later at **2026-09-12T09:43:11Z**, merge `6c5434c3a81cccac5cf2cde1be779c9c1c74b142`.
- Our own Sep-6 issue comment explicitly stated that **no GrantFox milestone/bounty application had been filed**.
- Several other contributors had already claimed/submitted work (#147, #342, #382, then #401).

**Disposition:** #401 is the actual closing/claim carrier. Preserve #363 as later maintainer-accepted related work; **prune #25's $90 from our bounty-claim basis**.

## #29 — $80 — CartProvider transition tests

Canonical issue title: `[Bounty: $80] Add unit tests for CartProvider count and total transitions`.

### Closing-carrier evidence

- Issue #29 is `closed / completed` at **2026-09-08T18:38:37Z**.
- The closing reference maps to commit `8005acef9090749dbed75226940c7e23391c8fda`.
- Commit message explicitly says:
  - `test(cart): add CartProvider count and total transition tests`
  - `Closes #29`
- GitHub maps it to **PR #414** by `realuca660-pixel`, merged at **2026-09-08T18:38:35Z**.
- PR #414 body is `Closes #29`.

### Our related work

- Our **PR #365** was already open before closure, but it was a narrower residual patch: it documented that six requested cases were already on `main` and added only the genuinely uncovered `clearCart` and cross-remount persistence cases.
- Our Sep-6 issue comment explicitly said the required pre-implementation GrantFox application had **not** been filed.
- PR #365 merged later at **2026-09-09T21:51:22Z**, merge `05b177c49c877c23988a972429d16ace94d5cc98`.
- Multiple competing submissions were already present (#236, #310, #389 and others).

**Disposition:** #414 is the bounty-closing carrier. Preserve #365 as later accepted test hardening; **prune #29's $80 from our bounty-claim basis**.

## Collection impact

The canonical `MOVA_2026-09-19.md` ledger must not treat advertised face value as entitlement. This Audit-A pass removes another **$270** from the plausible issue-bounty claim basis.

No conclusion is made here about compensation for later accepted related work outside the original issue bounty. Any such payment requires explicit maintainer/provider adjudication and remains under the existing root-owned Mova correspondence lane.
