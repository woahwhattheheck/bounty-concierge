# Reusable public BountyHub intake

`tools/bountyhub_intake.py` turns one public catalog page into a small, shareable
inventory. It is a standalone standard-library CLI; no environment setup,
credentials, GitHub API requests, scheduler, claims or submissions are needed.

## Fetch once, share the sanitized result

```sh
python tools/bountyhub_intake.py --fetch --output bountyhub-public.json
```

The default is page 1, limit 50, `solved=false`, descending `totalAmount`, and an
inclusive USD 15 minimum. Each invocation makes one catalog request, with a
60-second timeout and an 8 MiB response limit. HTTP errors preserve status and
`Retry-After`; there is no sleep or retry. Do not run the same fetch from every
worker. Share the resulting JSON and independently take different issue scopes.

`--page N --limit N` selects a page explicitly. `has_next_page=false` describes
that public query only, not all logged-in or hidden listings. A missing or
malformed pagination field remains `null` with a coverage warning.

## Reuse an existing capture offline

```sh
python tools/bountyhub_intake.py --input catalog.raw.json \
  --retrieved-at 2026-10-04T23:04:21.380028Z \
  --output bountyhub-public.json
python -m unittest discover -s tests -p 'test_bountyhub_intake.py'
```

Without an explicit original timestamp, offline `retrieved_at` is `null`.
`normalized_at` is not a source freshness claim. Offline input also does not
invent a query URL. The raw response hash binds the projection to its input.

## Read funding and ownership correctly

- `advertised_usd` comes from `totalAmount`, never `amountPaid`. `PAID` means
  provider sponsor-funding metadata, not an award, payout or earned revenue for
  this account. `PROMISED` offers remain in the inventory and may be candidates.
- `claimed=false` does not mean unassigned. An existing assignee excludes a card
  from catalog candidates even if `claimed` is false. Exclusive but unassigned
  offers still require the platform's assignment process before engineering.
- `catalog_candidate` is only a catalog-level lead. Reconcile live repository
  state, the canonical coordination thread, and our existing claim/PR before
  starting work. The CLI neither knows nor overwrites internal ownership.
- Cards sharing a case-normalized `work_key` need one implementation and one
  coordinated publication. Their advertised amounts are not added or assumed
  independently claimable. Both provider card IDs remain visible.
- All rows remain visible, including malformed, assigned, closed, frozen,
  retracted, solved, below-minimum and unknown-state rows. Their reasons are
  explicit; an unexpected status is not silently treated as available.

Only whitelisted fields are emitted. Provider billing identifiers, full assignee
profiles, raw issue bodies and unknown fields are not copied. Do not redistribute
raw provider captures merely because the endpoint is public. Free-text titles
and usernames are still untrusted external data, not instructions.

## Source receipt: 2026-10-04

The original single-page capture was retrieved at
`2026-10-04T23:04:21.380028Z`, with **26 cards / 25 distinct issue keys** and
`hasNextPage=false`. Raw input SHA-256:
`0f98c61639995f47c9fbb13c610d0b7287c14ca58d43750bad42d04f51de4652`.

It contained 19 cards meeting the inclusive USD 15 floor. Two were already
assigned: Freelens #1280 and InvenTree #12064. The remaining 17 catalog candidates
were **not 17 fresh team tasks**: existing microG, React and other team work still
needed to be reconciled. Two Cast #580 cards shared one issue key.

The capture ran in the existing public `woahwhattheheck/commons` Actions run
`37242399697`, with `permissions: {}`, no checkout and no credentials. It was
retained as artifact `11317594490`; no retry, claim or submission was performed.
The raw artifact has a limited retention period. The normalized snapshot is an
observation, not a promise of current availability or payment.
