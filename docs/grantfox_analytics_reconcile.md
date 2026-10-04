# Reconcile captured GrantFox released-payment rows

`concierge/grantfox_analytics_reconcile.py` reads a local capture of the
[official GrantFox Analytics dashboard](https://grantfox-analytics-olive.vercel.app/)
and reports payments attributed to specified GitHub handles. It makes no network
requests and does not modify a claim, account, payment, or settlement ledger.

## Run

```bash
python concierge/grantfox_analytics_reconcile.py \
  --snapshot /tmp/grantfox-public-snapshot.json \
  --claimant woahwhattheheck \
  --claimant tokenjunkielabs \
  --pr-url https://github.com/RemitFlow/RemitFlow-Backend/pull/142 \
  --pr-url https://github.com/StellarAgent-AI-Agent-Payment-Rails/Stellar-agentic/pull/426 \
  --output /tmp/grantfox-reconciliation.json
```

Repeat `--pr-url` for the existing contributions being reconciled. The command
also supports `python -m concierge.grantfox_analytics_reconcile` in a complete
checkout or installation. Omit `--output` for JSON on stdout; an output file is
created exclusively and an existing file is not overwritten. The only runtime
dependency is Python 3.9 or later.

## Input and source bounds

The input is the dashboard's captured JSON props, containing
`payments.items`, `payments.total_count`, `payments.truncated`, and
`summaries.all`. The summary supplies `currency`, `snapshot_at`, and
`contributor_payments`. Each payment carries `payment_id`, `username` (possibly
null), `amount`, project and campaign IDs/names, and `escrow_id`. Preserve the
source URL, capture metadata, and original page separately. The report hashes
the exact input file bytes; a local import does not authenticate those bytes.

The public dashboard sends up to 2,000 contributor rows to the page even when
only 25 rows are initially displayed. Its client-side campaign filter cannot
recover older rows omitted by that server limit. Read the existing published
page once and reuse the captured JSON; `/api/cron` and `/api/revalidate` perform
refreshes and are not collection read APIs.

[Official source](https://github.com/GrantChain/GrantFox-Analytics) identifies
released milestones using `flags.released === true`. Its public projection
omits PR URLs, wallets, payment dates, and transaction hashes. The underlying
Trustless Work milestone description includes a PR URL, but that separate
evidence is not present in this input. This command does not manufacture it.

## Reading the report

- `claimant_released_matches` uses exact, case-insensitive GitHub handles.
  Its subtotals describe the captured rows for those handles.
- `unmatched_pr_records` contains every supplied PR because this public input
  cannot link a payment to a PR. It means **unlinked**, not unpaid. PR URLs are
  operator supplied; this command does not verify that the handles authored them.
- `unresolved_candidates` lists claimant or unnamed rows whose project label
  exactly matches a supplied PR's repository owner. That label resemblance is
  weak association only. Candidate IDs can appear for multiple PRs; their
  amounts are never allocated to those PRs or added again to a subtotal.
- `unknown_username_payments` retains all rows without an attributed handle.
- `payer_history` groups observed rows by an exact project/owner label match.
  These subtotals include other contributors; they are not our earnings and do
  not independently establish a particular paid merge.
- `coverage` retains captured and reported counts, omitted rows, duplicate rows,
  unresolved names, and the provider's truncation flag. `snapshot_at` describes
  the source observation, not when an individual payment happened.

Amounts are parsed and summed with `Decimal` and exported as decimal strings.
Payment IDs are the deduplication key. Identical financial/attribution records
count once; conflicting duplicate IDs, malformed rows, invalid amounts,
duplicate JSON keys, inconsistent counts, and unsupported input return a clear
error and nonzero exit rather than a partial monetary report. No fuzzy handle,
avatar, alias, or repository-author inference is performed.

## Retained observation

The captured source snapshot dated **2026-10-04T06:05:48.805Z** reports **2,137**
released contributor payments and publishes **2,000** rows, leaving **137**
older rows outside this capture. **31** published rows have unresolved handles.
Neither `woahwhattheheck` nor `tokenjunkielabs` appears in the published rows.
The observed subtotal is therefore **0 USDC in this capture**. This does not
establish zero lifetime payouts, and no receipt of funds was verified.

Input SHA-256:
`ecec489da86c2fba970de34c0a398e29a6096ae277f552da577c231ad1d77c4a`.

A validation invocation processed that 1,042,650-byte capture and nine known PR
URLs in **95.95 ms**, including Python process startup, using Python 3.12.14.
It included a real positive comparison: `favvy0217` has one captured 60-USDC
release, while each owner handle has zero matching captured rows. That other
contributor's payment is kept separately in `observed_released_by_claimant` and
is not an owner earning. Its payment ID is
`CAWDIA4A4EMHCH5GTFUUPVKI2ZMKL6SYTD5A6OVNTPOPALYE62V3X4W4:5`.
The 19,543-byte result retained 2,000 unique rows, 137 omitted rows, 31 unresolved
usernames, and nine unlinked PR records. This is offline ingestion timing; no
provider lookup or payment action was included. Report SHA-256:
`637d3a6ae9b98d7303fc1232fab7dbf09f4f51818dac3b6b164e494c149ad7f0`.

The existing `concierge/grantfox_recognition_import.py` separately imports
captured GitHub merge/FoxPoints comments into the reward ledger. This command
does not convert points, acceptance, or an observed subtotal into a settlement.
