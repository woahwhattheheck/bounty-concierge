# Named-file catalog checkpoints

The existing catalog CLI accepts an optional `--output PATH` on its subcommands.
Without it, JSON continues to go to stdout with the same encoding, layout and
trailing newline. Diagnostics still go to stderr.

```sh
python -m concierge.bountyhub_catalog collect \
  --max-pages 3 --max-details 10 --min-funded-usd 15.00 \
  --output catalog.json

python -m concierge.bountyhub_catalog resume catalog.json \
  --max-details 5 --output catalog.json

python -m concierge.bountyhub_catalog targets catalog.json \
  --min-funded-usd 15.00 --output shortlist.json
```

Do not use `resume catalog.json > catalog.json`: the shell truncates the input
before Python starts. `--output catalog.json` reads the existing input first,
then serializes the result, writes a temporary file in the destination directory,
flushes and fsyncs it, closes it, and atomically replaces the destination.
The destination's parent directory must already exist. A newly replaced file
uses the temporary file's private permissions; existing ownership/ACL metadata
is not copied.

An input error, write failure or interruption before replacement leaves the
prior destination intact. Temporary-file cleanup is best effort. A hard kill
may leave a hidden staging file, but does not replace the destination with
partially written JSON. This is atomic visibility on the same filesystem, not
an interprocess lock, generation-order guarantee or power-loss durability
claim. Concurrent writers to one path remain last-replacement-wins; use
separate output paths for concurrent operations.

Partial reports are still saved and still return exit status **2**, with the
existing `PARTIAL` diagnostic. Complete results still return **0**. A filesystem
failure also returns **2**, so retain stderr and distinguish a saved partial
report from a failed write. A successful file write leaves stdout empty.

Use versioned output paths when earlier captures and error histories must be
retained. In-place resume intentionally replaces the prior file; the resume's
source digest does not recreate the old bytes. Keep target envelopes in a
separate file from full catalog reports. Output selection does not refresh
observations, change floors or retry limits, make extra provider requests, or
establish bounty eligibility/payment.

## Focused execution

```sh
python work/validation/catalog-atomic-output/check_output.py
```

Ten focused checks passed on Python 3.13.5 / Linux using the actual catalog
module and normal package imports. The collector's page transport is a
synthetic one-listing fixture; external requests are forbidden during the
check. Coverage: partial collection; same-path no-op resume with source digest,
observation and row preservation; target stdout/file parity; fsync/replace
failure; interruption; invalid input; missing parent; complete-target exit 0;
and JSON encoding/newline parity. No live catalog scan, hosted execution,
full-repository suite, Windows execution or earnings claim.

The executed catalog blob is `d9b6c6452278e98d9529649772e0cffd7a6265a4`.
This isolated source change was built on existing source artifact commit
`4f5bc01a9b6d6b91d5937d9743ed3608f9003ebc`, run `37199760399`, artifact
`11302078522`. Its branch intentionally uses that ancestor so a normal
three-way merge composes later catalog/resume/refresh/floor/exclusion work
instead of overwriting current main with the older source snapshot.
