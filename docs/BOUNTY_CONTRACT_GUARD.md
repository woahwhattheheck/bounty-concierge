# Bounty contract drift guard

Paid-work preflight answers whether a bounty is safe to dispatch at one point in
time. A pull request can take hours or days to build, review, and settle. During
that interval the source issue can be edited, relabeled, reassigned, closed, or
supplemented by a maintainer comment. Those changes can alter the work or the
advertised reward after effort has already begun.

`concierge.bounty_contract` creates a durable claim-time receipt and verifies it
against a later, stable GitHub generation. It is a conservative human-review
gate, not an acceptance or payment oracle.

## Authority boundary

The guard is read-only. It does not claim a bounty, post a comment, open or
submit a pull request, contact a sponsor, move wallet funds, or infer acceptance,
payout, or cash. `UNCHANGED` means only that the authority-relevant GitHub issue
terms still match the captured receipt. `HOLD` means a human must inspect the
change before work proceeds.

The durable receipt does **not** retain raw issue titles, issue bodies, milestone
text, maintainer comment text, or maintainer logins. It stores canonical source
identity, normalized public metadata, and SHA-256 fingerprints. This is enough
to detect movement without turning the receipt into a second mutable copy of the
bounty text.

## Capture after dispatch approval

Run the normal bounty preflight first. Once a human has approved dispatch, but
before claiming or beginning material work, capture the contract:

```bash
python -m concierge.bounty_contract capture Scottcjn/rustchain-bounties 1234 \
  --output receipts/rustchain-bounties-1234.json
```

Capture performs a stable double-read:

1. read the issue;
2. read every visible issue comment, with bounded pagination;
3. read the issue again;
4. read every comment again; and
5. read the issue a final time.

The receipt is emitted only when issue authority fields and the exact comment
ID/update generation remain stable throughout the read. A truncated, malformed,
or moving generation fails closed.

The contract fingerprint covers:

- canonical repository, issue number, GitHub database ID, node ID, and creation
  timestamp;
- issue author identity fingerprint and author association;
- open/closed state and state reason;
- conversation lock state;
- exact title and body fingerprints, including any advertised reward text;
- normalized label identities, names, colors, default flags, and description
  fingerprints, plus normalized assignee identities;
- milestone state, due date, and text fingerprints; and
- every OWNER, MEMBER, or COLLABORATOR issue comment, including author, body,
  and update fingerprints.

External contributor comments are read to establish a complete stable
comment generation, but they are not contract terms. New or edited external
comments therefore do not create a false contract-drift hold.

## Verify before irreversible stages

Verify the original receipt immediately before claiming substantial work,
submitting finished work, and routing settlement follow-up:

```bash
python -m concierge.bounty_contract verify \
  receipts/rustchain-bounties-1234.json \
  --output receipts/rustchain-bounties-1234.verify.json
```

Exit status is `0` for `UNCHANGED`, `2` for `HOLD`, and argparse's nonzero error
status when the request or transient GitHub read itself is unusable.

A `HOLD` receipt contains bounded reason codes such as:

- `TITLE_CHANGED`, `BODY_CHANGED`, or `LABELS_CHANGED`;
- `ASSIGNEES_CHANGED`, `MILESTONE_CHANGED`, or `ISSUE_STATE_CHANGED`;
- `ISSUE_AUTHORITY_CHANGED` or `MAINTAINER_TERMS_CHANGED`;
- `SOURCE_IDENTITY_CHANGED` or `SOURCE_UNAVAILABLE`;
- `LIVE_GENERATION_UNSTABLE`, `LIVE_EVIDENCE_INCOMPLETE`, or
  `LIVE_EVIDENCE_INVALID`.

Do not overwrite the original receipt after a hold. Inspect the issue history,
obtain human agreement on the changed terms, and preserve both the old receipt
and any explicitly approved replacement. Re-capturing without review erases the
very evidence this guard exists to preserve.

## JSON and automation

Both commands emit canonical, sorted JSON; wall-clock capture and verification
timestamps intentionally vary. Use `--compact` for one-line output and `-` to
read a verification receipt from standard input. SHA-256 provides deterministic
content fingerprints and envelope consistency checks, **not** a signature,
trusted timestamp, or proof of who created the artifact. The authority object
makes those limits explicit. Undeclared fields, noncanonical URLs, malformed
timestamps, duplicate normalized values, and mismatched digests are rejected.

Example summary fields:

```json
{
  "schema": "bounty-contract-verification/v1",
  "disposition": "HOLD",
  "reason_codes": ["BODY_CHANGED"],
  "authority": {
    "external_mutation_performed": false,
    "acceptance_inferred": false,
    "payout_inferred": false,
    "cash_claim": false,
    "time_attested": false,
    "signature": "none",
    "sha256_role": "consistency_not_authentication"
  }
}
```

Treat the artifact as one evidence layer alongside exact-head source review,
tests, maintainer acceptance evidence, and wallet settlement evidence. It does
not replace any of them.
