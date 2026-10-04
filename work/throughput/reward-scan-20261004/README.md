# Reward scan: numeric and whitespace scaling

The existing reward extractor retried unmatched numeric suffixes at every digit
or comma group. A single 4 KB issue-body fragment could consume more than one
second before reporting no amount. An adjacent pair of optional whitespace
matches after `reward` caused the same behavior on long whitespace.

`concierge/reward_evidence.py` now scans short amount indicators once. For a
currency indicator it finds the earliest valid preceding numeric suffix in a
backward pass, then uses the existing amount regex at that position. The previous
match's end bounds this search, preserving matches after a partially consumed
`$` or reward prefix. This retains exact text, spans, order, and legacy malformed
group substring behavior such as `1234,567 USD` yielding `234,567 USD`. Factoring
the prefix whitespace pattern preserves its accepted strings without repeatedly
partitioning the same whitespace.

The RTC amount and its left boundary now both use Unicode decimal digits in
the extractor and `bounty_index.parse_reward`. Whole Unicode amounts remain
supported. An attached or malformed Unicode number no longer leaks a shorter
suffix: `x١٢٣ RTC` is rejected just as `x123 RTC` already was.

## Reproduce

From the repository root, with baseline commit
`4ec4bee4b75fc1db313e183b343457a536286144` available in the local Git object store:

```sh
python -B work/throughput/reward-scan-20261004/benchmark.py > results.json
```

The script compares the actual old and new public extractors on all 266 retained
issues, covering 478,398 title/body characters. It also checks legacy RTC values,
62 exact general-match text/span/order cases, and Unicode boundary regressions.
Timing uses three-run medians and no timing assertions. It performs no provider
requests. The repository has no tracked general test suite at this baseline, so
this script is the focused repeatable regression entry point.

## Recorded results

These measurements are from one shared cloud Python process and measure elapsed
parser time, not provider latency or fleet throughput. Scheduling and load affect
absolute timings; results.json retains every size and measurement.

| Input near 4 KB | Before | After |
| --- | ---: | ---: |
| Unmatched digits | 1,262 ms | 0.900 ms |
| Comma groups | 292 ms | 0.866 ms |
| Decimal suffix | 1,052 ms | 0.449 ms |
| Reward prefix plus whitespace | 373 ms | 0.898 ms |
| Unicode digits | 1,931 ms | 0.848 ms |
| Broken final group before USD | 254 ms | 0.494 ms |

Patched inputs near 65 KB took 6.9–13.8 ms across those families. The separate
legacy `parse_reward` Unicode case fell from 656 ms to 0.433 ms at 4,096 digits.
All 266 retained evidence objects and legacy RTC values remained equal. These
stress inputs establish the removed backtracking behavior; they do not establish
an equivalent acceleration for ordinary issue text or end-to-end collection.

During measurement, independent keyword work changed `bounty_index.py` on main.
The candidate was then composed onto main
`15c048ff50e19c2bbec9d69326762b537fd720a7`, preserving that work. A fresh-import
check reconfirmed retained evidence and RTC behavior, and an AST comparison
confirmed that only its RTC pattern differs from that current main snapshot.
The timing and integration source hashes are recorded separately in results.json.

On the actual retained 266-row index, three-run median batch parsing was
74.99 ms before and 92.38 ms after (0.81x). This separate
measurement used the composed current source, and retained complete output
parity. It is recorded separately from the stress measurements.
