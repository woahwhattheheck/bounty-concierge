# MOVA role progression

The `concierge-mova-progress` command advances existing valid MOVA single or batch manifests using exact role-completion receipts. It does not make provider calls, grant authorizations, or submit claims.

Example:
  concierge-mova paid-candidates.json --batch --owners wave-owners.json --output wave.json
  printf '[]\n' > receipts.json
  concierge-mova-progress wave.json --receipts receipts.json --output next.json

A receipt is a JSON object containing these required fields:
- schema: mova-role-receipt/v1
- target_key: exact source packet key, for example owner/repo#42
- operation_id and packet_sha256: exact source packet values
- lease_key, role, owner: exact assigned role packet values
- receipt_id: unique stable identifier (a repeated identical receipt is idempotent)
- evidence_sha256: 64 lowercase hex digits of saved evidence
- receipt_type and status: the role-specific success markers listed below

Successful stage markers:
SCOUT: SCOUT_RECEIPT / COMPLETE
BUILD: BUILD_RECEIPT / COMPLETE
QA: QA_ACCEPT_RECEIPT / ACCEPTED
PUBLISH: PUBLICATION_RECEIPT / COMPLETE
COLLECT: COLLECT_RECEIPT / COMPLETE

BUILD, QA and PUBLISH additionally require head_sha (40 lowercase Git hex); QA and PUBLISH heads must equal the BUILD head. PUBLISH also needs publication_url for a PR in the original bounty repository.

The output preserves the original role owner, account, lease and claim obligations and returns exactly one next role per bounty. Possible statuses are OWNER_REQUIRED, READY_FOR_INDEPENDENT_PROVIDER_RECHECK, and COMPLETE. The returned readiness status does not establish sponsor assignment, validated provenance, provider acceptance, award or payout.

Invalid manifest hashes, mismatched leases, conflicting receipt assertions, missing intermediate stages, rejected QA, and source-head drift fail closed (exit 2). Input receipts are not independently authenticated: recheck canonical Slack/GitHub/platform evidence before any provider mutation. Never delete an adverse receipt to advance the pipeline.
