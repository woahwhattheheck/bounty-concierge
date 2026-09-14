# Distribution fulfillment gate

Some paid-work issues are not satisfied by a repository artifact alone. A source can require a real public, off-platform delivery URL before a human claim is ready. This gate keeps “the deliverable exists” from being mistaken for “the payout condition has been satisfied.”

The gate is deliberately **read-only**. It does not publish content, contact maintainers, submit claims, create accounts, follow redirects, infer acceptance, authorize payout, mutate a wallet, or recognize revenue. It emits a deterministic `READY_FOR_HUMAN_DISTRIBUTION_SUBMISSION` or `HOLD` receipt plus a reward-prioritized queue.

## Trust model

The decision has two separate input planes:

1. **Request evidence** — auditable source/rule/submission/capture material. Treat this JSON as attacker-controlled.
2. **Retained verifier authority** — `distribution-fulfillment-authority/v2`, authenticated with HMAC-SHA256 under a host key retained outside the request/authority caller's control.

A capture self-hash is only an integrity checksum. It is **not provenance**. READY is impossible unless the retained authority authenticates the exact advertised-reward text plus the normalized source snapshot, maintainer rule, submission packet, live capture, verifier identity, canonical provider namespace, provider resource identity, content digest, issuance time, and active key ID.

The production CLI never accepts:

- an authority-record path;
- an authority-record directory;
- an authority HMAC key;
- a key ID;
- an evaluation timestamp.

The retained-authority CLI is intentionally POSIX-only so authority files can be acquired with descriptor-relative `O_NOFOLLOW` semantics. It reads the active key only from:

```text
/var/lib/bounty-concierge/distribution-fulfillment/authority-key.json
```

and source-derived records only from:

```text
/var/lib/bounty-concierge/distribution-fulfillment/records/<sha256(canonical-source-url)>.json
```

Non-POSIX retained-authority execution fails closed rather than falling back to check-then-open pathname semantics.

Each key/record read first opens its trusted parent directory with `O_DIRECTORY|O_NOFOLLOW`, verifies owner/mode policy, retains that directory descriptor, then opens the final regular file relative to that descriptor with `O_NOFOLLOW` and reads from the held file descriptor. Parent path rebinding after acquisition therefore cannot redirect the read. Trusted parents must not be group/other writable; retained files must exclude all group/other permission bits. Deploy the fixed root under an operator/service account boundary whose untrusted request producer cannot write. The HMAC key file is exactly:

```json
{
  "schema": "distribution-fulfillment-authority-key/v1",
  "key_id": "ops-2026-09",
  "key_hex": "<64 lowercase hex characters>"
}
```

A trusted capture/verifier integration can call `issue_authority_record(...)` with the 32-byte host key. There is intentionally no CLI signing command. Persist the resulting authority record at the source-derived path before evaluation.

## Authority projections

For a request with a live capture, the authenticated record binds:

- canonical source URL after case-folding GitHub owner/repository identity;
- exact accepted advertised-reward plain-decimal text;
- normalized source snapshot SHA-256 projection;
- normalized maintainer-rule SHA-256 projection;
- normalized submission-packet SHA-256 projection;
- normalized live-capture SHA-256 projection;
- verifier identity;
- canonical **provider namespace** (for example `social:x` or `video:youtube`);
- canonical provider `resource_identity`;
- content SHA-256;
- verifier issuance time;
- active host key ID.

For a request without a capture, provider/resource/content/verifier scope is null and the missing URL becomes a business HOLD.

The verifier issuance time must not be in the future and must follow the source/rule/capture evidence it authenticates.

## Freshness and immutable negative decisions

Source snapshots, maintainer rules, and live captures have a seven-day freshness window. READY receipts carry `valid_until` equal to the earliest evidence expiry and cannot verify as current READY authority after that instant.

A stale evidence item is different: it produces a canonical **HOLD**. HOLD receipts remain integrity-verifiable after `valid_until`, including when `valid_until` was already in the past at evaluation time. This does not grant stale evidence positive authority; deterministic replay must still reproduce the same HOLD. The distinction is intentional:

- receipt integrity answers “is this exactly the negative decision that was made?”;
- READY freshness answers “is this positive readiness decision still current?”

A stale HOLD cannot be edited or replayed into READY because the receipt hash, authenticated authority projection, and deterministic re-evaluation must all agree.

## Cross-claim collision policy

Exact resolved-URL reuse still holds all affected claim units. Provider-resource and provider-content reuse are stricter: their queue keys are based on the **authenticated provider namespace**, not on the capture verifier identity or resolved host.

Therefore two different capture systems observing URL aliases for the same `social:x` resource cannot escape the reuse fence merely by using different verifier IDs or hosts. The trusted upstream verifier is responsible for assigning a canonical provider namespace/resource identity.

Collision HOLD codes are:

- `LIVE_URL_REUSED_ACROSS_CLAIMS`
- `LIVE_RESOURCE_REUSED_ACROSS_CLAIMS`
- `LIVE_CONTENT_REUSED_ACROSS_CLAIMS`

The single-request receipt is immutable; queue-level collision policy lives in separately hashed queue items.

## Request evidence

`distribution-fulfillment-request/v1` contains:

- canonical GitHub issue identity (owner/repository case-folded so route aliases dedupe) and a bounded, non-negative **plain** advertised-reward decimal string (no exponent notation);
- source state, labels, update/capture times, and snapshot SHA-256;
- maintainer-rule comment on that issue, capture time, live-URL requirement, allowed off-platform hosts, and rule digests;
- a human-only `bounty-submission-packet-item/v1` that cannot infer acceptance/payout/cash;
- optional live capture binding requested/final URL, canonical provider resource identity, observation time, HTTP status, public resolvability, content SHA-256, verifier ID, and capture self-hash.

Malformed authority-bearing input raises an error. Ordinary negative business state such as a closed source, absent label, missing URL, stale evidence, unsuccessful response, private URL, or disallowed host produces HOLD.

GitHub-hosted URLs are structurally rejected for an off-platform requirement. The module never fetches a URL itself; network/SSRF handling remains a separate verifier boundary.

## Authority ceiling

Every receipt states:

- `external_post_performed=false`
- `claim_submission_authorized=false`
- `maintainer_acceptance_inferred=false`
- `payout_inferred=false`
- `revenue_recognized=false`
- `human_submission_review_required=true`

READY therefore means only that authenticated current evidence clears this distribution-readiness gate. A human still decides whether/how to publish or claim.

## CLI

Evaluate one request using the fixed retained authority store and the host clock:

```bash
python -m concierge.distribution_fulfillment evaluate request.json
```

Build a reward-prioritized queue from a JSON array:

```bash
python -m concierge.distribution_fulfillment queue requests.json
```

Verify an immutable receipt:

```bash
python -m concierge.distribution_fulfillment verify request.json receipt.json
```

Verify a queue:

```bash
python -m concierge.distribution_fulfillment verify-queue requests.json queue.json
```

Input JSON rejects duplicate object keys. Timestamps use canonical UTC second precision. Exact JSON types matter; integer `0` cannot impersonate boolean `false`.

## Security regression suite

`tests/test_distribution_fulfillment.py` includes hostile coverage for:

- forging both request evidence and an authority record under an attacker key;
- caller-selected key IDs;
- semantic request tampering under a retained authority record;
- fabricated self-hash captures;
- provider namespace tampering;
- two different verifier IDs plus URL aliases for the same provider resource/content;
- stale source/rule/capture HOLD receipt and queue verification;
- READY expiry;
- authority issuance ordering/future time;
- exact type replay;
- host/redirect/resource validation;
- duplicate JSON keys and non-finite JSON;
- CLI rejection of caller-supplied authority paths;
- POSIX retained-descriptor private-file/symlink fences and adversarial parent-path rebinding;
- authenticated reward tamper and exponent-expansion denial;
- GitHub owner/repository case-alias collapse across retained paths and queue claim units;
- source-derived retained-record paths.
