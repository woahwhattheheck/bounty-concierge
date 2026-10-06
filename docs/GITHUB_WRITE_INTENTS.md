# Durable GitHub write intents

`concierge.github_write_intent` preserves a **specific, already-decided PR-body
update** when the GitHub write rail cannot execute it immediately. It does not
call GitHub and does not replace the existing cooldown, breaker, availability,
or collision guards.

Use it when a publisher has already fenced an existing pull request and knows:

- the repository and PR number;
- the exact expected head SHA;
- the complete replacement body;
- a stable operation ID.

The ledger stores that immutable payload in a private SQLite file. Reusing an
operation ID with different coordinates, head, or body fails closed. Only one
publisher may hold the intent lease at a time; an abandoned lease can be
reclaimed after expiry.

## Minimal flow

```bash
export CONCIERGE_GITHUB_WRITE_INTENTS=/private/swarm/github-write-intents.sqlite

python -m concierge.github_write_intent enqueue \
  --operation-id sanctifier-988-body-r1 \
  --repository Centurylong/sanctifier \
  --pull-number 988 \
  --expected-head 67bbc3573761d918c746c021c9ef54935360cce2 \
  --body-file /private/work/pr-body.md

python -m concierge.github_write_intent claim \
  --owner publisher-seat-17 --lease-seconds 90
```

A successful `claim` returns the exact body because that worker now owns the
bounded provider attempt. Before sending the PATCH, re-read the PR and compare
its head with `expected_head`.

If the rail is hot, return the intent to the queue with the provider-derived
deadline instead of retrying:

```bash
python -m concierge.github_write_intent defer \
  --operation-id sanctifier-988-body-r1 \
  --owner publisher-seat-17 \
  --not-before-epoch 1791269000 \
  --error-code PRIMARY_RATE_LIMIT
```

After a successful PATCH and exact-head readback:

```bash
python -m concierge.github_write_intent complete \
  --operation-id sanctifier-988-body-r1 \
  --owner publisher-seat-17 \
  --observed-head 67bbc3573761d918c746c021c9ef54935360cce2
```

Completion rejects head drift and leaves the lease intact for explicit
reconciliation. `list` omits body content while exposing state, checksum,
retry floor, and lease metadata.

## Boundaries

- PR-body updates only; no issue comments, source writes, merges, or claims.
- No credentials or provider responses are stored.
- No automatic retries and no scheduler.
- Provider retry/reset evidence belongs in the existing cooldown/breaker rails;
  this ledger only keeps the mutation durable until a healthy publisher claims it.
