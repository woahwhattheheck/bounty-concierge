# Claim-work authority recovery hardening

This note records the security boundary added during recovery of the additive claim/work authority primitive from stale PR #164 onto current main.

Original product/source credit remains **Z-OrbitLattice-2203-V9K2 (`ZOL-V9K2`)**. Recovery/finalization is **Z-OsmiumLedger-2026-N8Q5 (`ZOL-N8Q5`)**.

## Why the recovery shim exists

The source primitive correctly removed caller-controlled HTTP transport and bound a sponsor claim unit to a live merged GitHub PR. Two authority seams still prevented safe durable use:

1. relationship authorization and retained provider-receipt authentication used the same host HMAC key; and
2. the downstream boundary would accept a sponsor-adjudication report while the upstream explicit test-only unsigned mode was enabled.

The stale branch also retained tests that still passed the removed public `session=` argument, so those tests no longer exercised the actual entrypoint.

## Installed invariants

`concierge.claim_work_authority_hardening` is installed from `concierge.__init__` before normal callers receive the claim-work module.

It enforces:

- `BOUNTY_CLAIM_WORK_AUTHORITY_KEY_HEX` signs only fresh claim-unit/work relation authority;
- `BOUNTY_CLAIM_WORK_PROVIDER_RECEIPT_KEY_HEX` authenticates only retained live-provider receipts;
- the two keys must be different;
- missing, malformed, or reused receipt key fails closed;
- `BOUNTY_SPONSOR_ADJUDICATION_TEST_ONLY_ALLOW_UNSIGNED=1` is rejected before sponsor report verification and causes historical/current receipt verification to fail;
- literal `0` remains equivalent to the upstream bypass being disabled;
- `bind_claim_work` retains exactly one public parameter, `payload`;
- `verify_claim_work_current` retains exactly two public parameters, `payload` and `receipt`;
- GitHub HTTP transport remains module-owned and cannot be supplied by caller input.

## Receipt threat model

A holder of the relation-signing key can authorize a fresh relation but cannot forge a retained GitHub provider receipt. A holder of a receipt can recompute the public `receipt_sha256`, but cannot recompute `host_receipt_hmac_sha256` without the independent receipt key.

The receipt key should remain stable for the historical verification horizon. Rotating it intentionally invalidates old receipts unless a separate migration/reissue mechanism is introduced; this module does not silently fall back to the relation key.

## Test evidence expected before merge

The dedicated workflow runs on Python 3.9 and 3.13, in normal and `python -O` modes, and covers at minimum:

- valid relation + merged-provider binding;
- no public transport injection;
- relation-key receipt forgery rejection;
- identical/malformed receipt-key rejection;
- upstream unsigned-test-mode rejection across binding/historical/current paths;
- invalid/stale relation authority rejection before network;
- open/unmerged/head-drift provider rejection;
- redirect/non-200/response-URL rejection;
- provider exception redaction;
- GitHub token non-export;
- public receipt reseal resistance;
- current provider reacquisition; and
- explicit read-only/no-send/no-payment authority ceiling.
