# Bounty contract-terms negative preflight

**Purpose:** Stop contributor time from entering provably noncash or contractually unilateral bounty tasks. This is an **offline, negative-only** classifier of already captured public issue, README/CONTRIBUTING and listing text. It does not read GitHub, contact anyone, register claims, or adjudicate fraud.

Run before a paid scout hands any candidate to the MOVA sprint factory. Feed a JSON object with `repository_full_name`, `issue_number`, and one or more `issue_body`, `contributor_terms`, `listing_text`; `issue_title` is optional. Capture the actual URLs, retrieval time, SHA/version and payer proof in the existing central registry separately. Run:

```sh
python -m concierge.bounty_contract_terms_preflight --input candidate.json
```

The output always has `authorization_to_build=false`. `EXCLUDE_NONCASH` detects explicit mock-payment/test mode; `HOLD_CONTRACT_RISK` detects unilateral internal competition or required prework GrantFox acknowledgment; `HOLD_PROVIDER_PROOF` flags automatic-on-merge claims involving a named provider, requiring verification directly from that provider; `NO_HAZARD_DETECTED_NOT_APPROVED` means **no known indicator in this text**, not eligibility. Invalid or missing captures fail closed with exit code 2.

Evidence is source-field SHA-256 plus **fixed, bounded indicators**, not arbitrary untrusted issue text or credentials. These patterns deliberately under-match ambiguous language rather than imply dishonesty. Human review must determine scope, the exact payable agreement and whether a clause actually applies. Never write a public fraud label from this output; do not waive any already-submitted original-author contribution.

**Non-negotiable separate gate:** check the owner-maintained payer registry and live exact payer/repository/platform/task approval before further bounty labor, new claims or unpaid revisions. Sponsor activity, a merge, sponsor-funded escrow or payout setup is not proof that a contributor received funds. Existing claims and collection continue independently.

Real issue examples: [UltimateAI #14](https://github.com/iyeanur6-cyber/ultimate-ai-platform/issues/14) (unilateral internal development race), [Mova Store contributing terms](https://github.com/Movalabs-crew/mova-store/blob/main/CONTRIBUTING.md) (pre-work acknowledgment), [Claude Builders #3](https://github.com/claude-builders-bounty/claude-builders-bounty/issues/3) (advertised auto payment requiring independent verification), [OpenBounty public site](https://openbounty.ai/) (mock/no-cash mode). These illustrate risks, not findings of payment default.
