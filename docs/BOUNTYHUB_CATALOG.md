# BountyHub catalog to preflight

`concierge.bountyhub_catalog` connects the public BountyHub listing API to the
existing GitHub preflight and batch-capture tools. It does not need a GitHub token
or BountyHub login for catalog reads. Run one collection and share its reduced
report; other workers can derive their shortlist from that report without
repeating the provider reads.

## Collect once

```bash
python -m concierge.bountyhub_catalog collect \
  --max-pages 10 --page-size 100 --max-details 50 --min-funded-usd 25.00 > catalog.json
```

The collector uses one `requests.Session`, reads ascending numbered pages from
`https://api.bountyhub.dev/api/bounties`, and follows the returned `hasNextPage`
flag. Each page requests `limit=100` by default; `--page-size` accepts 1 through
100 and is recorded in the report. It then reads pledge/claim details only for
nonterminal listings whose
advertised total can reach the selected funding floor. The default floor is
$25. The page and detail bounds are independent; no retry, sleep, watcher,
schedule, claim, proposal, account change or other provider write is performed.

`catalog.json` contains every collected listing, source observation start/end,
request/page/detail counts, declared traversal coverage, reduced funding data,
and a `shortlist`. Its observation time belongs to this collection and does not
move when the file is reused. A complete traversal means the returned pages and
selected details were read; it is not an atomic snapshot of the changing service.

## Include promised rewards explicitly

The default shortlist uses only `reported_funded_usd`. Under the
[October 4 owner instruction](https://tokenjunkielabs.slack.com/archives/C0BU51F1PL3/p1791104588390399),
a payer with evidence of an actual paid merge is GREEN even when the current
offer is not funded or escrowed. The current offered reward must still be at
least $25, and current work availability, claimant eligibility, required
assignment steps, and existing submission ownership still apply. A merged PR
or advertised reward alone is not paid-merge evidence; reuse the payer's
recorded payment/settlement evidence or authoritative paid record.

Use the existing `--include-promised` option to discover those offers, or when
the work order otherwise explicitly permits promised rewards:

```bash
python -m concierge.bountyhub_catalog collect \
  --include-promised --min-reward-usd 25.00 > catalog.json

python -m concierge.bountyhub_catalog targets catalog.json \
  --include-promised --min-reward-usd 25.00 > shortlist.json
```

This mode applies the floor to each resolved listing's funded plus promised
amount. A listing with $0 funded and $50 promised can therefore qualify, while
its two reported amounts remain distinct. Marked payouts, unknown payment
statuses, unresolved details and terminal listing exclusions are unchanged.
No new detail reads are needed when selecting from an existing report.

`--min-reward-usd` and `--min-funded-usd` are aliases for the same floor; the
chosen mode determines which amounts count. In the new mode, the selected
result records `reward_basis: "reported_funded_plus_promised"` and
`minimum_reward_usd` instead of `minimum_funded_usd`. The default mode remains
funded-only. `collect` includes those fields in `shortlist`; `targets` reports them
to stderr while preserving the batch parser's exact candidates envelope.
Programmatic callers can use `select_targets(report, "25.00", include_promised=True)`
to obtain the same selection and its complete `listing_ids_by_issue` associations.
Promised amounts remain promises; this option does not establish funding,
assignment, eligibility, award or payment.

The catalog retains aggregate amounts, not payer identities or payment-history
records. This option therefore expands discovery; it does not classify every
returned sponsor as GREEN. Before acting on an unfunded offer, bind the current
offer's payer to the recorded paid-merge evidence in the existing work-order
thread. Do not infer that identity from the repository owner, apply one payer's
history to another contributor's pledge, or treat the aggregate promised total
as verified compensation from a GREEN payer. No new payer audit is needed when
the same payer's evidence is already recorded.

## Use the existing batch capture

```bash
python -m concierge.bountyhub_catalog targets catalog.json > shortlist.json

python -m concierge.bounty_capture_batch shortlist.json \
  --output-dir ./captures-20261004 \
  --max-issues 25 --max-pages 10 --max-requests 100 --json
```

Choose a new output directory for each batch; its parent must already exist.
`targets` writes the batch parser's exact `{"candidates": [...]}` envelope, with
only `repo` and `number` in each row. It performs no network requests. The
original observation times, coverage and candidate count are reported to stderr;
retain the complete catalog beside the shortlist for funding and listing
provenance. Use `targets --min-funded-usd AMOUNT` to select a higher floor from the
same report. Lowering the floor can expose listings whose details were not
collected; those stay unresolved and make the selection partial.

The collector deduplicates GitHub issue targets without collapsing BountyHub
listing identities. `listings` keeps every distinct pool, while
`shortlist.listing_ids_by_issue` binds each selected issue to its qualifying
listing IDs. Each listing must independently meet the selected floor: funded
alone by default, or funded plus promised with `--include-promised`. Separate
pool amounts are not silently added together. For example, Cast #580 has two
separate listings, which must keep separate claim/payment records.

The batch's existing canonical issue, contribution terms, assignment, competition
and generation checks remain authoritative for preflight. Catalog flags and
funding are discovery evidence. In particular, BountyHub's top-level `claimed`
flag can be false while many open claim records exist. A listing may also lag a
closed GitHub issue or an exclusive assignment. This shortlist does not establish
that work is unclaimed, that our account is eligible, or that money has been paid
to us. Existing issue-level contributors and submission owners retain their work.

## Funding reduction

The detail endpoint's `pledges` array already contains the creator's original
pledge. The collector sums that array once; it does not add the listing's
`amount` again. It ignores `amountPaid`, which includes creator-side fees.

Only nonretracted, nondeleted pledge records enter the aggregate:

| Output | Source meaning |
| --- | --- |
| `reported_funded_usd` | Pledge `amount` whose `paymentStatus` is `PAID` |
| `reported_promised_usd` | Pledge `amount` whose status is `PROMISED` |
| `other_payment_status_usd` | Amount carrying any other payment status |
| `payout_marked_usd` | Amount of pledge records whose `isPaid` flag is true |
| `active_pledge_count` | Number of included pledge records |
| `claim_count`, `open_claim_count` | Aggregate current claim counts, with rejected claims excluded from the open count |

Unknown payment-status amounts and marked payouts are not presented as available
funding for new work. A mismatch between pledge totals and the detail's advertised
total leaves that detail invalid. Amount arithmetic uses decimal strings and
`Decimal`, with no binary-float conversion. None of these fields proves a transfer
to our account, a successful claim or sponsor acceptance.

For the actual RCS detail read on October 4, 2026, the three active pledge records
were $5,000 paid, $5,000 paid and $4,999 promised. The reduced result was therefore
$10,000 funded and $4,999 promised, matching its $14,999 total. Adding the creator
amount again would incorrectly produce $19,999.

## Partial reads and reuse

Successful complete collection/selection exits 0. A page/detail bound, HTTP or
transport failure, malformed payload, repeated listing during pagination, or
unresolved selected funding exits 2. Collected rows remain in the JSON report;
an interrupted or failed traversal is not an empty queue. HTTP failures stop
further reads. A 429, or 403 carrying `Retry-After`, records rate limiting, and
numeric/date retry guidance is retained without automatically retrying.

`catalog_complete` and `details_complete` distinguish the two acquisition phases.
`shortlist.source_complete` additionally records whether selected funding is
resolved. These refer to the configured collection scope. The commands above are
separate so an operator can inspect retained partial results before deliberately
passing their known targets to the existing batch. Never treat a nonzero exit as
a complete census or reset the saved observation timestamp to make it current.

## Resume interrupted funding details

After respecting provider and shared-workspace cooldowns, finish only missing
funding details from a report whose catalog traversal completed:

```bash
# Use a NEW output filename; shell redirection must not truncate the source.
python -m concierge.bountyhub_catalog resume catalog.partial.json \
  --max-details 10 > catalog.resumed.json
python -m concierge.bountyhub_catalog targets catalog.resumed.json > shortlist.json
```

`resume` inherits the source report's collection floor and explicit promised-reward
mode. It accepts no new floor or reward-mode switch. In particular, an old $50
capture is not silently expanded by the current $25 default. Use a new `collect`
for a changed scope or current catalog facts. `resume_catalog(snapshot,
max_details=10)` is the equivalent programmatic entrypoint.

The complete retained catalog is validated before any provider read. A missing or
unfinished catalog traversal, inconsistent identity/funding totals, duplicate
listing ID, or CLI input over 4 MiB is rejected. A resume before the retained
observation time is refused before new reads. Detail
URLs are reconstructed from the canonical API and validated UUIDs, never followed
from an arbitrary saved URL. Existing `COMPLETE` details are not fetched again;
only active listings meeting the original floor with unresolved details consume
the new 0-100 request budget. A completed input or zero budget opens no session.
The existing detail reader still continues past 404/410 removals and stops on
other HTTP/transport failures; no automatic retry loop, sleep or schedule is added.
Any recorded `Retry-After` prevents early resumption, measured conservatively
from the prior observation end. Missing retry guidance is not permission to ignore
provider limits: the operator must respect any current shared cooldown before
explicitly invoking this command.

Keep both files. The new report retains the original `started_at`, successful
listing evidence and order. `catalog_observed_through` permanently records the
initial report's end as an upper bound on catalog observation, and
`shortlist.catalog_refreshed` is false. Only a resume that attempts detail reads
advances the overall `completed_at`; this is the cumulative observation interval,
not a refreshed catalog. A no-op preserves observation times and cooldown fields.
Top-level request/detail counts are cumulative; `resume.requests_made` and
`resume.details_fetched` describe only this invocation. The receipt separately
records invocation times, source observation times, original error count and a
SHA-256 of the source's canonical JSON (`sort_keys=True`, compact separators,
finite JSON values). `errors` contains this invocation's errors; preserve the
source file for previous errors. Additional resumes bind the immediately prior
report rather than embedding an unbounded history.

`targets` retains its exact batch-compatible stdout envelope and additionally
reports the retained catalog bound and `catalog_refreshed=false` to stderr.
Complete traversal/selected funding exits 0; unresolved details or selected
funding still exits 2. Completed but ineligible funding is not refreshed merely
by resuming. These are mixed-time discovery observations, not a fresh census,
assignment, accepted contribution, current availability or payment receipt.

### Reproduce the request reduction without provider traffic

```bash
git show b3d0d0f5f6526f4b592627e157ee19fe40caca45:concierge/bountyhub_catalog.py \
  > /tmp/bountyhub-catalog-before.py
python work/benchmarks/bountyhub-catalog-resume-280b.py \
  --baseline /tmp/bountyhub-catalog-before.py \
  --candidate concierge/bountyhub_catalog.py \
  --output /tmp/bountyhub-catalog-resume-results.json
```

The bounded replay imports both complete production modules and runs the real
CLI, replacing only provider transport and the clock with controlled responses.
On Python 3.13.5 with requests 2.32.5, 26 synthetic listings / 17 selected details
were interrupted by a 429 after 12 successful details. Restart required 18 GETs;
resume required 5, a 72.22% reduction in recovery requests. Including the failed
initial attempt, total requests fell from 32 to 19 (40.62%). The resulting reduced
listing rows and ordering matched the uninterrupted baseline. Ten focused check
groups passed, including unchanged original collection behavior, cooldowns,
bounded continuation, no-op behavior, malformed input and partial CLI exits.
Raw results: [bountyhub-catalog-resume-280b.json](../work/benchmarks/bountyhub-catalog-resume-280b.json).
This is a synthetic request-count measurement with zero network requests, not a
live-provider or fleet-latency benchmark. No new availability or reward claim is
made and the existing GitHub preflight remains required.

## Export and measurement boundary

The report is constructed from an allowlist. It omits issue bodies, contribution
prose, raw pledge/claim/assignment objects, account profiles, checkout-session IDs,
payment-order IDs and fee-inclusive payment amounts. Retain raw API responses
privately if needed for debugging; publish the reduced report instead.

The initial retained collection in
`work/supply/bountyhub/2026-10-04-catalog-56f3.json` read 26 listings on three pages
and 17 selected details in 20 public GET requests. The actual CLI took 12.429
seconds in the shared cloud harness. Seven issue targets met the reported funded
floor before GitHub/claim preflight. These are dated observations and one measured
run, not current availability, a latency guarantee or earnings. That initial run
used the provider's default page size of 10. C19E's subsequent retained public
`page=1&limit=100` response contained the same 26 distinct listing IDs with
`hasNextPage=false`. The collector now defaults to that larger page size: one
catalog-page read instead of three for the observed catalog, with pledge-detail
reads still required. This reduces the corresponding request count from 20 to
18 if the selected details are unchanged; it is not a new wall-clock benchmark.
Re-exporting a retained collector report requires zero provider reads and
preserves the recorded interval.

```bash
python -m concierge.bountyhub_catalog targets \
  work/supply/bountyhub/2026-10-04-catalog-56f3.json > shortlist.json
```

This existing capture demonstrates the bridge and can support source inspection.
Run a new collection and the normal current preflight when current provider facts
are required; the checked-in observation never becomes fresh merely by reuse.
