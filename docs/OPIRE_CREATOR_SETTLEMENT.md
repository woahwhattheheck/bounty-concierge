# Opire-listed contributions: collect from the creator, not Opire

**Provider correction, received from the Opire Team October 8, 2026:** Opire says it no longer processes bounty payments or manages bounty claims. It is a listing board for creators' pledges, with payment arranged **directly between creator and developer**. `/try`, `/claim`, a PR merge and a listed dollar amount are not bank-verified payments. Do not assume old automated-payment or claim-registration instructions work. Confirm the actual sponsor's terms before new paid BUILD intake. In particular, a contributor cannot manufacture funding by adding a dollar-labelled issue.

## Offline MOVA COLLECT packet

Once original contributor identity, the canonical sponsor issue and the actual original-author PR are independently checked, supply an evidence record:

```json
{
  "platform": "Opire",
  "repo": "fixture-owner/fixture-repo",
  "issue_number": 42,
  "issue_url": "https://github.com/fixture-owner/fixture-repo/issues/42",
  "pr_url": "https://github.com/fixture-owner/fixture-repo/pull/43",
  "original_author": "woahwhattheheck",
  "advertised_usd": "65"
}
```

Run `python -m concierge.opire_creator_settlement receipt.json --output packet.json`. The output includes a creator-directed **draft** affirmative compensation request, evidence pointers, a list of outstanding acceptance/terms/transfer steps, and `bank_verified_paid_usd: null`. The tool uses no network, no credentials and no publication method. Add `sponsor_acceptance_evidence_url`, `creator_payment_terms_evidence_url`, or `transfer_evidence_url` only with canonical verified receipts. The tool validates URL syntax, *not* their substantive provenance; even supplied receipt pointers cannot auto-convert advertised USD into paid USD.

Operator flow: **SCOUT** authenticates sponsor/payer and funding; **BUILD** implements accepted issue scope; **QA** performs only contract-relevant checks; **PUBLISH** retains original contributor account and submission URL; **COLLECT** reviews sponsor acceptance, requests direct payment with the packet draft, confirms actual transfer through authorized account records, then separately updates the authoritative earned/received ledger. Never mark received from a platform badge, old bot command or self-supplied evidence pointer. Keep private payment evidence on an authorized shared internal surface, not a public GitHub PR.
