# Distribution fulfillment gate

Some paid-work issues require a real public, off-platform delivery URL before a human submission can be considered complete. This gate keeps a finished repository artifact from being promoted to a distribution-ready claim when the external-distribution evidence is missing, stale, ambiguous, or not independently bound.

The gate is deliberately **read-only**. It does not post content, contact maintainers, submit claims, create social accounts, follow redirects, infer acceptance, authorize payout, or recognize revenue.

## Two-plane authority model

`distribution-fulfillment-request/v1` is the auditable business-evidence plane. It contains:

- the canonical GitHub issue URL and exact advertised-reward decimal string;
- a source snapshot (`state`, labels, source update/capture times, snapshot SHA-256);
- the maintainer-rule comment on that issue, its content/rule digests, capture time, live-URL requirement, and off-platform host allowlist;
- the human-only `bounty-submission-packet-item/v1`;
- optionally a live capture binding requested/final URL, **provider resource identity**, observation time, HTTP status, public resolvability, content SHA-256, verifier identity, and capture-envelope digest.

`distribution-fulfillment-authority/v1` is a separate authority plane. It carries exact SHA-256 commitments to the normalized source, rule, submission-packet, and live-capture decision projections plus the expected capture-verifier identity. **These bindings must be retained independently from the trusted upstream/capture verification boundary. Never generate the authority file from the untrusted request at consumption time.** A same-digest semantic edit in the request therefore cannot inherit old authority merely by preserving an opaque upstream hash.

The live-capture envelope's self-hash is only an integrity check. It does not prove that a public-web verifier observed the URL. READY requires the independently retained capture decision binding and verifier identity as well.

## Trusted time and freshness

The production CLI captures current UTC itself. It no longer accepts a caller-chosen evaluation timestamp.

Source snapshot, maintainer rule, and live capture each have a seven-day maximum age. A live capture must not predate the source snapshot or maintainer-rule capture that authorizes the distribution requirement. Receipts carry `valid_until`, the earliest expiry across authority-driving evidence.

`verify_receipt()` reconstructs the receipt at its original evaluation instant using the retained authority bindings, performs **type-exact canonical JSON comparison**, and separately enforces `valid_until` against the current trusted time. A receipt that was READY at T0 therefore cannot verify indefinitely after its evidence expires.

## Receipts and queue decisions

Single-request receipts use `distribution-fulfillment-receipt/v2`. They are immutable after evaluation and remain independently verifiable.

Queue-level collision policy is expressed separately with `distribution-fulfillment-queue-item/v1` decisions inside `distribution-fulfillment-queue/v2`; the queue never rewrites and re-hashes a single-request receipt. `verify_queue()` deterministically reconstructs the complete queue decision and verifies every embedded receipt at consumption time.

A queue HOLDs all affected claims when it detects any of:

- exact resolved-URL reuse;
- verifier + provider-resource-identity reuse, including URL aliases;
- same content SHA-256 under the same verifier and resolved host.

The provider resource identity must be emitted by the hardened capture boundary (for example a canonical provider post/video/artifact identity), not guessed by this gate.

## Important HOLDs

The gate also holds when the source is closed, the `distribution` label is absent, the base submission packet is not ready, source/rule/capture evidence is stale or future, the live capture predates its authorizing source/rule evidence, the live URL is missing, HTTP resolution is unsuccessful, public resolution is false, or the requested/final host is outside the maintainer-rule allowlist.

GitHub-hosted URLs are structurally rejected for an off-platform requirement. Malformed authority-bearing input fails closed with an error rather than being coerced.

## Authority ceiling

Every single-request receipt states:

- `external_post_performed=false`
- `claim_submission_authorized=false`
- `maintainer_acceptance_inferred=false`
- `payout_inferred=false`
- `revenue_recognized=false`
- `human_submission_review_required=true`

A READY receipt means only that the independently bound evidence clears this distribution-readiness contract. A human still decides whether and how to submit the claim.

## CLI

Evaluate one request with independently retained authority bindings:

```bash
python -m concierge.distribution_fulfillment evaluate request.json authority.json
```

Build a reward-prioritized queue. `authorities-by-source.json` is an object keyed by canonical source URL:

```bash
python -m concierge.distribution_fulfillment queue requests.json authorities-by-source.json
```

Verify a single-request receipt at current trusted time:

```bash
python -m concierge.distribution_fulfillment verify request.json authority.json receipt.json
```

Verify a complete queue:

```bash
python -m concierge.distribution_fulfillment verify-queue requests.json authorities-by-source.json queue.json
```

Input JSON rejects duplicate object keys. Timestamps use canonical UTC second precision. Exact JSON types matter: boolean `false`, integer `0`, and float `0.0` are not interchangeable authority values.

## Network boundary

This package intentionally does not fetch arbitrary URLs. Fetching user-controlled URLs remains a separate SSRF/network-security boundary. The hardened verifier must perform the live read and retain the out-of-band authority commitment that is later supplied to this gate.
