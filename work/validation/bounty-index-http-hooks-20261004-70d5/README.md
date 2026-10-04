# Bounty index HTTP response-hook repair

Harbor-70D5-Circuit / GPT-6 Astra Pro / ChatGPT cloud harness, October 4, 2026.

A Requests response hook can raise `HTTPError` before `Session.get` returns.
The index collector previously classified every such exception as a transport
failure, discarded HTTP status and retry guidance, and continued to later
repositories. The attached response also missed the normal close path.

The production change in `concierge/bounty_index.py::fetch_bounties_report`
retains an attached `HTTPError.response` with an explicit `is None` check and
passes it through the existing quota/authentication classifier and
`finally: response.close()`. An error Response is falsey. A separate flag keeps
a custom hook's rejection of an HTTP 200/304 response classified as an error.
Response-less failures keep their existing sanitized transport behavior.

## Source pins

| Source | Git revision |
| --- | --- |
| Baseline commit | `b79630d662ff635b277b71e0b662615fa185d0b6` |
| Baseline index blob | `e73c3d3f881998321d9453108deab4e03b9555ef` |
| Candidate index blob | `63535f23b0c90945517b2e3f64fbca8212f09e8a` |

The production diff is two hunks, 11 additions and 5 deletions. The receipt also
records the actual index blob hashes used in each run.

## Reproduce the focused replay

From a repository checkout containing the pinned source objects, stage only the
baseline module and its three direct dependencies. No duplicate source snapshot
is included with this replay.

```bash
replay_dir=$(mktemp -d)
mkdir -p "$replay_dir/concierge"
for module in bounty_index bounty_cache config reward_evidence
do
  git show "b79630d662ff635b277b71e0b662615fa185d0b6:concierge/$module.py" > "$replay_dir/concierge/$module.py"
done
git cat-file blob 63535f23b0c90945517b2e3f64fbca8212f09e8a > "$replay_dir/candidate.py"

python3 work/validation/bounty-index-http-hooks-20261004-70d5/replay.py \
  --baseline-root "$replay_dir" \
  --candidate "$replay_dir/candidate.py" \
  --output "$replay_dir/replay-results.json"
```

To evaluate a later checkout, pass `--candidate concierge/bounty_index.py`
instead. The receipt reports that file's actual blob hash. `--output` is
optional; JSON is always printed to stdout. The command exits 1 if any candidate
case fails and 0 when all eight pass. Baseline failures are expected.

The script loads the complete production index module and its direct dependencies
without package-level policy bootstrap. It uses real Requests Sessions, response
hooks, and Response objects, with HTTPS transport replaced by an in-memory
adapter. No provider requests or socket connections are made. The synthetic token
is local replay input.

## Observed outcomes

Execution used Python **3.12.14** and Requests **2.34.2**. Each case supplies three
repositories. Baseline: **1/8 passed**. Candidate: **8/8 passed**.
Full receipt: [replay-results.json](replay-results.json).

| Case | Baseline | Candidate |
| --- | --- | --- |
| First response 429 | 3 GET attempts; transport error; quota guidance lost | 1 GET; later sources deferred; delay 17 and reset retained |
| First response throttled 403 | 3 GET attempts; transport error; quota guidance lost | 1 GET; later sources deferred; delay/reset retained |
| First response authenticated 401 | 3 GET attempts; transport error | 1 GET; later sources marked authentication-deferred |
| Ordinary permission 403 | 3 GET attempts; later two rows retained; transport classification | 3 GET attempts; later two rows retained; HTTP classification |
| Ordinary 404 | 3 GET attempts; later two rows retained; transport classification | 3 GET attempts; later two rows retained; HTTP classification |
| Response-less HTTPError | Sanitized transport failure; later repositories read | Same behavior |
| Success followed by 429 | 3 GET attempts; third repository still read | 2 GET attempts; first repository's row retained |
| Hook rejects attached 200 | Transport error; rejected response unclosed | HTTP error; rejected response closed |

Every attached candidate response was closed exactly once. In the baseline, each
hook-rejected attached response had zero closes. The response-less case has no
attached response to close.

The path-argument publication update was confirmed once using the same eight
cases. It retained the recorded production blob hashes and pass counts; the
optional output file matched stdout byte for byte.

## Limits

This is a bounded execution of the exception-handling path with controlled
responses. It does not measure live-provider latency, fleet-wide rate-limit
savings, installed package bootstrap, TLS behavior, or new bounty earnings.
Cache and pagination logic were outside this small change. No retry loop, sleep,
new limiter, dependency installation, general test suite, or hosted test job was
added.
