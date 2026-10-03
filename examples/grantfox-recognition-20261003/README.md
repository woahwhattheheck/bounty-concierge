# GrantFox recognition from two merged contributions

This is a usable ledger input assembled from four public GitHub API records observed on October 3, 2026 at 06:53:24 UTC. The captured response JSON is retained in `sources/`; each source hash in `input.json` binds those exact newline-terminated file bytes.

| Work | Merge commit | Merge time (UTC) | Provider award | Reported account total |
| --- | --- | --- | ---: | ---: |
| [Stellar-agentic #426](https://github.com/StellarAgent-AI-Agent-Payment-Rails/Stellar-agentic/pull/426) | `fa24e5c7113df6fd58d4c3ada99ef7a637e1d542` | 2026-10-02 14:34:57 | 35 FoxPoints | 35 points |
| [Stellar-agentic #427](https://github.com/StellarAgent-AI-Agent-Payment-Rails/Stellar-agentic/pull/427) | `74ac46cb503777e37e48d2213ba442b423406f17` | 2026-10-02 14:44:38 | 35 FoxPoints | 70 points |

Both provider comments name `tokenjunkielabs` as the contributor and `grantfox-oss[bot]` as the author:

- [Issue #318, comment 5954751622](https://github.com/StellarAgent-AI-Agent-Payment-Rails/Stellar-agentic/issues/318#issuecomment-5954751622), captured in `sources/comment426.json`.
- [Issue #327, comment 5954906363](https://github.com/StellarAgent-AI-Agent-Payment-Rails/Stellar-agentic/issues/327#issuecomment-5954906363), captured in `sources/comment427.json`.

The corresponding merge records are `sources/pr426.json` and `sources/pr427.json`. Source references use the exact GitHub API record URLs. Award IDs use `github-comment:<id>` to identify the individual provider decision; they do not imply a separate payment or reward transaction ID.

## Import captured provider records

The importer turns captured GitHub PR/comment JSON pairs directly into the
existing ledger input format. It extracts the merge, claimant, individual
FoxPoints award and reported account total, and hashes the exact source files.
For this retained batch, use its original observation time:

```bash
python concierge/grantfox_recognition_import.py \
  --observed-at 2026-10-03T06:53:24Z \
  --pair examples/grantfox-recognition-20261003/sources/pr426.json \
         examples/grantfox-recognition-20261003/sources/comment426.json \
  --pair examples/grantfox-recognition-20261003/sources/pr427.json \
         examples/grantfox-recognition-20261003/sources/comment427.json \
  --output /tmp/grantfox-imported-input.json

python concierge/reward_settlement_ledger.py compile \
  --input /tmp/grantfox-imported-input.json \
  --out-dir /tmp/grantfox-imported-ledger
```

Choose unused output paths. `--output` creates a file exclusively; omit it for
JSON on stdout. Both scripts also support `python -m concierge.<module>` in a
complete checkout or installation. Repeat `--pair` for another captured batch,
using that batch's observation time. This reads local records; it does not fetch
GitHub or change any provider state.

Source IDs use GitHub object IDs and case IDs use repository/PR identity, so
filenames and pair order do not affect identity. Repeated identical pairs count
once. Different captures of the same object in one observation batch, mismatched
PR/claimant pairs and unsupported comment formats return a clear error and
nonzero exit. The importer recognizes the retained GrantFox merge/award template;
other provider formats continue to use the existing input contract. The supplied
GitHub records remain operator-captured evidence, not independently authenticated
by this local importer.

The actual four-file batch (50,964 bytes) imported and compiled successfully in
97.75 ms in one local run, including both Python process launches. Every imported
fact and source hash matched the retained hand-assembled input; generated case
and source IDs use the stable provider identities described above. Output held
70 FoxPoints, two `MERGED_UNSETTLED` cases and empty monetary aggregates. This is
local evidence ingestion timing, with no network or payment execution.

## Run the retained input

From the repository root, choose a fresh output directory:

```bash
python -m concierge.reward_settlement_ledger compile \
  --input examples/grantfox-recognition-20261003/input.json \
  --out-dir /tmp/grantfox-recognition

python -m concierge.reward_settlement_ledger verify \
  --input examples/grantfox-recognition-20261003/input.json \
  --ledger /tmp/grantfox-recognition/ledger.json \
  --markdown /tmp/grantfox-recognition/ledger.md \
  --receipt /tmp/grantfox-recognition/receipt.json
```

The standalone entry point `python concierge/reward_settlement_ledger.py` accepts the same arguments when using only this module's source and the captured records.

Expected result: **70 FoxPoints across two awards**, with each reported account total retained as an observation. Both work items remain `MERGED_UNSETTLED`; monetary award and eligibility are unevidenced, all currency aggregates are empty, and no payment is asserted. The 70-point total in the second comment is never added to the 35 + 35 awards.

This historical example records provider recognition at the observation time. It does not establish a dollar conversion, payout profile readiness, cash award or receipt of funds. Updating the example requires fresh provider/merge records and their corresponding file hashes; do not change quantities or work identities without source evidence.
