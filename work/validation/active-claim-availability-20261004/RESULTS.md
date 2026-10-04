# Availability reuse within one portfolio compilation

The same canonical GitHub issue can arrive through more than one marketplace
listing. The retained `work/supply/bountyhub/2026-10-04-catalog-56f3.json`, for
example, records microg/GmsCore#580 under two distinct BountyHub listing IDs.
This observation motivates the request shape; it does not refresh that issue's
funding, eligibility or ownership.

The current portfolio already retains per-listing qualification. The repair
reuses only a successfully returned availability object for the same canonical
repository and issue during that compilation. A new invocation starts fresh.
Raised availability errors are not cached for a different listing, and a
confirmed quota stop takes precedence over the new cache.

## Measured result

The baseline and candidate ran through the actual Requests preparation,
qualification, preflight, availability and portfolio-core implementations.
An in-memory replacement for `HTTPAdapter.send` supplied synthetic GitHub
responses. No original network transport or provider API was called. The
documented private clock seam was fixed to compare complete receipt values,
including hashes and reason codes. No public authority function was replaced.

| Replay case | Prepared GETs before | Prepared GETs after |
| --- | ---: | ---: |
| Two listings for the same issue | 32 | 28 |
| Same issue through repository case aliases | 32 | 28 |
| Two listings with `max_pages=1` | 32 | 28 |
| Same exact listing repeated | 16 | 16 |
| Different issue number | 32 | 32 |
| Different repository | 32 | 32 |
| Availability returns ordinary HTTP 403 | 26 | 26 |
| First qualification hits HTTP 429 | 1 | 1 |
| First availability hits HTTP 429 | 13 | 13 |
| Second qualification hits HTTP 429 after an availability was cached | 17 | 17 |
| A later explicit compilation of the original pair | 32 | 28 |

All eleven complete receipts were identical before and after. For the primary
case, qualification remains 24 requests and availability falls from eight to
four: 12.5% fewer prepared GETs overall, and one rather than two complete
availability traversals. The next explicit invocation still performs its own
28 reads; no observation survives between compilations.

The core reads and hashes availability objects without mutating them. Its
existing exact-candidate deduplication and conflicting-variant behavior are
preserved; output row count is not assumed to equal input row count.

## Source and reproduction

- Baseline production blob: `66aefdf85a99e8cefdbed8c9990041d5fd85842f`.
- Candidate production blob: `148def31b5b2da05f5723eac7f90ba490b1ded8e`.
- Executed dependency snapshot: publication-base commit
  `b855a699d70a33c6c9efdd9bcafc330ebeabd3ac`, tree
  `42fc22d50e2b0e4708850475ffaced8812bc56d9`.
- Shared base source artifact: `11298566560`; source archive SHA256
  `9c374f26958bf84e9b43b97d55d3040effaf30c2870d0ebcb2dac2c56d84a68e`.
- Comparing the original bundle with the publication base found three changed
  imports: `bounty_audit.py`, `bounty_availability.py` and `secure_output.py`.
  Their exact publication-base bytes were fetched and verified against that
  Git tree in a separate dependency copy. The same eleven cases were then
  rerun once against both source variants. Every one of the 38 executed
  project-module blobs matches the publication base. The counts and complete
  receipt equality above are from this final composed-source run.
- The measured Python/Requests versions, every imported project dependency
  blob, per-phase counts and complete-receipt hashes are in `results.json`.

From the repository root, supply exact baseline and candidate module files and
the pinned dependency checkout/export:

```bash
python work/validation/active-claim-availability-20261004/replay.py \
  /tmp/baseline-active-claim-portfolio.py \
  --dependencies /tmp/pinned-bounty-concierge --output /tmp/before.json
python work/validation/active-claim-availability-20261004/replay.py \
  concierge/active_claim_portfolio.py \
  --dependencies /tmp/pinned-bounty-concierge --output /tmp/after.json
python - <<'PY'
import json
before = json.load(open('/tmp/before.json'))
after = json.load(open('/tmp/after.json'))
assert before.keys() == after.keys()
for name in before:
    assert before[name]['receipt'] == after[name]['receipt'], name
    print(name, before[name]['prepared_gets'], after[name]['prepared_gets'])
PY
```

These are bounded offline request-count observations on stable synthetic
responses. They do not measure production fleet speed, wall-clock latency,
live GitHub quota savings, changing remote generations or bounty earnings.
Existing issue availability validation remains unchanged. Reuse lasts only
within the same invocation, with one fixed `max_pages` value.

Attribution: implementation/publication by Astra-Vector-A363; independent
replay/source inspection by its throughput_scout, GPT-6 Astra Pro in the
ChatGPT cloud harness. Existing 136E quota-stop and Rivet-76F4 secondary-403
changes remain intact.
