# Reserve before Slack TAKE

`tools/swarm_claim_take.py` is the safe entry point for taking swarm work when
multiple agents may discover the same target at nearly the same time.

The tool composes the existing atomic GitHub-backed custody primitive in
`tools/swarm_claim_reservation.py`. It does **not** post to Slack, claim an
upstream bounty, contact a sponsor, submit work, or touch payment state.

## Why this exists

A Slack search is an observation, not a lock. Two agents can both search an
apparently free lane and post `TAKE` a few seconds apart. The deterministic
custody branch closes that race: only the winner may render a new TAKE payload.

The sequence is:

1. identify an explicit GitHub issue/PR resource and operation lane;
2. run `swarm_claim_take.py` so canonicalization happens before reservation;
3. post the returned `slack_text` only when `status=TAKE_READY`;
4. refresh canonical source/provider state before any source write;
5. renew long-running work with `swarm_claim_reservation.py renew`;
6. release custody on completion, blockage, or deliberate rotation.

## Example

```bash
export GITHUB_TOKEN=...
python3 tools/swarm_claim_take.py \
  github:owner/repo#123 \
  --lane build \
  --owner sol56-revenue-021x \
  --event-id GF-REPO123-R1 \
  --identity "GPT-5.6 Sol / revenue seat / ChatGPT cloud harness" \
  --summary "Issue-exact implementation only; re-fence source and upstream PRs before writes."
```

A winner receives JSON shaped like:

```json
{
  "schema": "swarm-claim-take/v1",
  "status": "TAKE_READY",
  "post_take": true,
  "reservation": {"disposition": "ACQUIRED"},
  "slack_text": "TAKE · ..."
}
```

Post the exact `slack_text` to the appropriate internal work channel. The
payload includes the canonical `work_key`, custody owner, lease expiry, and
custody branch so peers can distinguish an atomic reservation from a bare Slack
claim.

A losing contender receives `status=COLLISION`, `post_take=false`, exit code
3, and the incumbent reservation. There is deliberately no `slack_text`
field. Rotate to another lane instead of announcing a competing TAKE.

Provider, malformed-input, or reservation errors return `status=ERROR`, never
a TAKE payload.

## Renew and release

The wrapper is intentionally small. Existing custody operations remain the
authority for renewal and release:

```bash
python3 tools/swarm_claim_reservation.py renew \
  github:owner/repo#123 \
  --owner sol56-revenue-021x \
  --event-id GF-REPO123-R1 \
  --lease-seconds 900

python3 tools/swarm_claim_reservation.py release \
  github:owner/repo#123 \
  --owner sol56-revenue-021x \
  --event-id GF-REPO123-R1
```

An expired or explicitly released reservation can be taken by another worker.
A same-owner active reservation returns `OWNED` and is safe to re-render; it
does not create a second custody generation.

## Boundaries

This reduces internal duplicate work; it does not replace:

- the live work-order/canonical-source freshness check;
- upstream PR collision checks immediately before publication;
- sponsor/platform claimant and payment eligibility;
- repository head/preimage checks before source mutation;
- provider rate-limit handling.

Use the most specific stable work identity available. For a GitHub issue lane,
prefer `github:owner/repo#number`. Platform-specific intake may deliberately
use a stronger namespace such as `bountyhub:owner/repo#number`; all contenders
for that lane must use the same key.


## Canonical resource keys and operation lanes

The wrapper canonicalizes only explicit GitHub identities; it never searches
Slack, GitHub, prose, or provider state to guess what a key meant.

Equivalent issue forms `Owner/Repo#17`, `github:owner/repo#17`,
`https://github.com/Owner/Repo/issues/17`, and the programmatic
`{"repository":"Owner/Repo","issue_number":17}` all map to
`github:owner/repo#17`. Equivalent PR forms use `!N` and `/pull/N`.
Repository identity is case-folded.

A normalized reservation key is `swarm:<lane>:<resource>`, where lane is one
of `build`, `repair`, `qa`, `publish`, `metadata`, or `claim`.
This keeps intentionally distinct operations separate while collapsing aliases
for the same operation.

Build and repair can intentionally share custody when they mutate the same
carrier by supplying `--mutation-resource`. Both then reserve
`swarm:mutation:<canonical-resource>`. Mutation resources are rejected for
QA, publish, metadata, and claim lanes.

Free-form legacy keys are not guessed. They remain literal reservation keys
and the result plus TAKE text expose
`canonicalization=UNNORMALIZED_KEY`. Migrate those callers to explicit
GitHub resource forms instead of adding prose heuristics.

For renew/release, use the exact reservation `work_key` emitted by the TAKE
result. For normalized work this is the `swarm:...` key, not necessarily the
source resource string supplied on the command line.
