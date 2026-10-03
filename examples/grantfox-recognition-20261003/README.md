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

## Run

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
