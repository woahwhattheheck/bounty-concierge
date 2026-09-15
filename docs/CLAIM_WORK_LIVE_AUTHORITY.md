# Claim-unit → merged-work live authority

`concierge.claim_work_authority` is a read-only trust boundary between sponsor adjudication and downstream collection.

A sponsor-authenticated `REWARD_OFFERED` proves commercial sponsor state. It does **not** prove that an arbitrary caller-written GitHub PR is the work for that claim unit. Likewise, `{"state":"MERGED"}` in local JSON is not GitHub merge evidence.

This primitive requires both facts before emitting `MERGED_WORK_BOUND`:

1. a retained `sponsor_adjudication.verify_report()` report and a **fresh host HMAC** authorizing the exact claim-unit → work relationship; and
2. a **live read-only GitHub PR readback** proving the exact repository, PR number, expected head SHA, target repository, and merge state.

The output is sealed with a **second, independent host key** so possession of relation-signing authority cannot mint or rewrite a durable provider receipt.

## Boundary

This module can:

- verify a retained sponsor-adjudication report;
- select exactly one canonical, non-collapsed claim unit;
- derive the exact relation scope that a trusted host must authorize;
- verify the host relation HMAC and reject stale/future captures;
- issue one internally owned `GET https://api.github.com/repos/{owner}/{repo}/pulls/{number}` with redirects disabled;
- verify exact PR identity, merged state, expected head SHA, base repository, merge commit SHA, and merge timestamp;
- mint a separately HMAC-sealed historical receipt;
- re-acquire current host relation authority + current GitHub readback when a downstream action needs current proof.

It cannot:

- accept an HTTP session/transport from caller input;
- contact a sponsor/customer;
- submit or update a bounty claim;
- mutate GitHub;
- initiate/request a payout;
- mutate a payment provider, bank, or wallet;
- infer cash, payment, debt, or recognized revenue;
- authorize a collection send by itself.

`receipt["authority"]["collection_send_authorized"]` is always `false`.

## Input

```json
{
  "schema": "bounty-claim-work-live-bind-input/v1",
  "adjudication_report": {"...": "retained authority-verified report"},
  "claim_unit_id": "unit-1",
  "work": {
    "repo": "owner/repository",
    "pr": 42,
    "head_sha": "0123456789abcdef0123456789abcdef01234567"
  },
  "relation_evidence": {
    "ref": "owner-review:ticket-42",
    "sha256": "...64 lowercase hex..."
  },
  "relation_authority": {
    "provider": "owner-host",
    "principal_sha256": "...64 lowercase hex...",
    "captured_at": "2026-09-14T02:20:00Z",
    "scope_sha256": "...64 lowercase hex...",
    "signature_sha256": "...64 lowercase hex..."
  }
}
```

The caller does **not** provide a `state`, canonical GitHub URL, merge timestamp, merge commit, HTTP client, or either host key. Provider facts are derived only from the internally owned live GitHub request.

## Host configuration

The relationship verifier reads only host environment:

- `BOUNTY_CLAIM_WORK_AUTHORITY_KEY_HEX`: exactly 32 bytes / 64 lowercase hex; relation authorization only;
- `BOUNTY_CLAIM_WORK_PROVIDER_RECEIPT_KEY_HEX`: exactly 32 bytes / 64 lowercase hex; durable provider-receipt authentication only;
- `BOUNTY_CLAIM_WORK_AUTHORIZED_PROVIDER`: bounded provider identifier;
- `BOUNTY_CLAIM_WORK_AUTHORIZED_PRINCIPAL_SHA256`: exact authorized principal digest;
- optional `GITHUB_TOKEN`: bearer token for the read-only GitHub request. It is never copied into a receipt or exception message.

The two HMAC keys **must be different**. Configuration that reuses the relation key as the receipt key fails closed. Keep the receipt key stable for the historical verification horizon; deliberate key rotation requires an explicit receipt migration/reissue policy rather than silently accepting receipts under the relation key.

The sponsor-adjudication environment variable `BOUNTY_SPONSOR_ADJUDICATION_TEST_ONLY_ALLOW_UNSIGNED=1` is forbidden at this boundary. It exists only for predecessor semantic tests. Claim/work scope derivation, binding, historical verification, and current verification all fail closed while that unsigned mode is enabled. A literal `0` does not enable the upstream bypass and is accepted for deterministic host/test configuration.

## Authorizing a relationship

The trusted adapter first calls:

```python
scope = compute_relation_scope(report, claim_unit_id, work, relation_evidence)
```

That method verifies the retained sponsor report and canonical claim-unit membership. The adapter then captures current UTC seconds and computes:

```text
HMAC-SHA256(
  relation_key,
  canonical_json({
    "schema": "bounty-claim-work-relation-authority/v1",
    "provider": configured_provider,
    "principal_sha256": configured_principal_sha256,
    "captured_at": captured_at,
    "scope_sha256": scope.scope_sha256
  })
)
```

Canonical JSON is UTF-8, sorted keys, separators `(',', ':')`, no NaN/Infinity.

A new binding rejects authority that is in the future or older than 300 seconds. Changing the adjudication report generation, claim-unit membership, work repo/PR/head, relation reference, or relation evidence digest changes `scope_sha256` and invalidates the HMAC **before GitHub is contacted**.

## GitHub proof

`bind_claim_work(payload)` derives exactly one API URL from the authorized `repo` and `pr`. The function has no public transport/session argument. Redirects are disabled. HTTP must be exactly 200. The response must prove:

- `html_url == https://github.com/{repo}/pull/{pr}`;
- exact integer PR number;
- `state == "closed"` and `merged is true`;
- `head.sha == authorized head_sha`;
- `base.repo.full_name == authorized repo`;
- lowercase 40-hex `merge_commit_sha`;
- canonical UTC `merged_at` that is not in the future.

Failures are source-text-free: provider exception bodies/messages are never reflected to the caller.

## Durable receipt vs current verification

`bind_claim_work()` produces a receipt with two integrity layers:

- public deterministic `receipt_sha256` catches accidental corruption;
- `host_receipt_hmac_sha256`, keyed by `BOUNTY_CLAIM_WORK_PROVIDER_RECEIPT_KEY_HEX`, prevents a relation-authority signer or receipt holder from changing the provider result, claim membership, relation metadata, or authority ceiling and then recomputing the public digest.

`verify_claim_work_receipt(receipt)` is **historical**. It verifies the independent retained-receipt HMAC and public digest but intentionally does not reapply the five-minute relation freshness window.

`verify_claim_work_current(payload, receipt)` is **current-action** verification. It requires a fresh relationship authorization, re-reads GitHub through the module-owned transport, and compares the stable bound generation. Downstream collection should use this current verifier immediately before treating the relation as current authority.

Both verification paths refuse sponsor-adjudication unsigned-test mode.

## Downstream integration rule

A collection renderer that wants to state `Merged work:` or `Exact merged head:` as fact should consume a valid claim-work receipt/current verification rather than caller-authored work fields. The receipt proves two independent boundaries:

- the trusted relation host authorized *this exact work identity* as the relation for *this exact sponsor claim-unit generation*; and
- GitHub independently proved that exact work identity is merged, with the retained provider receipt authenticated by a distinct key.

It does **not** itself prove that payment is due or authorize a send.
