# Distribution fulfillment gate

Some paid-work issues are no longer satisfied by a repository artifact alone. A source may carry a `distribution` label and a maintainer rule requiring a real public, off-platform delivery URL before the work counts toward payout. This gate keeps a completed asset/code packet from being mistaken for a payable claim when that external distribution step is still missing.

The gate is deliberately **read-only and deterministic**. It does not post content, contact maintainers, submit claims, create social accounts, follow redirects, infer acceptance, or recognize revenue. It consumes three independently bound facts: the current source-issue snapshot, the maintainer's explicit distribution rule, and a live-URL capture produced by a separate public-web verifier. It then emits either `READY_FOR_HUMAN_DISTRIBUTION_SUBMISSION` or `HOLD` with stable reason codes.

## Why this exists

A common failure mode is "the deliverable exists, therefore the bounty is claimable." That is false when the maintainer has added a live-distribution condition. The commercial consequence is worse than a normal validation miss: it creates duplicate/incomplete outreach, makes revenue forecasts optimistic, and wastes operator attention on claims that cannot yet settle.

This layer addresses that conversion boundary without granting itself posting or payout authority.

## Inputs

`distribution-fulfillment-request/v1` binds:

- a canonical GitHub issue URL and exact advertised-reward decimal string;
- a current source snapshot (`state`, labels, source update/capture times, snapshot SHA-256);
- a maintainer-rule comment on that exact issue, with content/rule digests, capture time, `requires_live_url=true`, and an explicit off-platform host allowlist;
- a downstream `bounty-submission-packet-item/v1` that is still human-only and cannot infer acceptance/payout/cash;
- optionally, a live capture that binds the requested URL, final resolved URL, observation time, HTTP status, public resolvability, content SHA-256, verifier identity, and a digest of the exact capture envelope.

Malformed authority-bearing input raises an error. Missing or stale business evidence produces `HOLD` instead of an exception.

## Important HOLDs

The gate holds when the source is closed, the `distribution` label is absent, the base submission packet is not ready, the live URL is missing, the capture is stale/future, HTTP resolution is unsuccessful, public resolution is false, or the requested/final host is outside the maintainer-rule allowlist.

GitHub-hosted URLs are structurally rejected for an off-platform requirement. A portfolio queue also rejects reuse of one final live URL across multiple bounty claims.

## Authority ceiling

Every receipt states:

- `external_post_performed=false`
- `claim_submission_authorized=false`
- `maintainer_acceptance_inferred=false`
- `payout_inferred=false`
- `revenue_recognized=false`
- `human_submission_review_required=true`

A READY receipt therefore means only: the captured evidence clears this distribution-readiness contract. A human still decides whether and how to submit the claim.

## CLI

Evaluate one request:

```bash
python -m concierge.distribution_fulfillment evaluate request.json \
  --evaluated-at 2026-09-13T10:00:00Z
```

Build a reward-prioritized conversion queue:

```bash
python -m concierge.distribution_fulfillment queue requests.json \
  --evaluated-at 2026-09-13T10:00:00Z
```

Verify an immutable single-request receipt by deterministic replay:

```bash
python -m concierge.distribution_fulfillment verify request.json receipt.json \
  --evaluated-at 2026-09-13T10:00:00Z
```

Input JSON rejects duplicate object keys. Timestamps use canonical UTC second precision. Exact JSON types matter; for example, integer `0` cannot impersonate boolean `false` in authority evidence.

## Live capture boundary

This package intentionally does not fetch arbitrary URLs. Fetching user-controlled URLs is a separate network/SSRF/security boundary and should remain in a hardened public-web verifier. The verifier must create the exact capture envelope consumed here; the envelope digest is recomputed before any READY decision.
