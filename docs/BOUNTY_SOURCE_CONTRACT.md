# Bounty source-contract preflight

Canonical bounty qualification answers whether a bounty is eligible to dispatch.
Some open, unassigned, otherwise-actionable issues can still be stale against the
current source tree: an issue may ask to merge files that were already removed, or
name a symbol that no longer exists. Sending those rows directly to builders wastes
GitHub quota and swarm seats.

concierge.bounty_source_contract adds a narrow, fail-closed check for that case.

## When to use it

Use this only when a bounty work order depends on explicit current-source
assumptions you can state without guessing, for example:

- consolidate two existing test files;
- modify an existing helper in a named source file;
- move logic out of a named existing module.

Do not scrape or infer requirements from arbitrary issue prose. A new file named in
acceptance criteria is not a required-existing file. If the source assumption is
ambiguous, leave the row behind the normal canonical preflight/lease and resolve the
source contract manually.

## Command

Example:

    python -m concierge.bounty_source_contract \
      --repo Commitlabs-Org/Commitlabs-Frontend \
      --ref <fresh-canonical-main-sha> \
      --require-file src/lib/backend/validation.ts \
      --require-literal 'src/lib/backend/validation.ts::validateCommitmentId' \
      --json

The command resolves --ref once, then reads only the explicitly required files at
that immutable commit. It performs no retries and no repository crawl.

- READY / exit 0: every required file exists and every path-bound literal is present.
- STALE / exit 2: a required file or literal is absent.
- ERROR / exit 2: provider state is ambiguous (rate limit, malformed response,
  undecodable file, oversized literal-check file, etc.). Treat this as not
  dispatchable, not as proof that the source is stale.

The JSON receipt binds repository, requested ref, resolved commit, file blob SHAs,
file sizes, presence booleans, SHA-256s of required literals, and a receipt SHA-256.
Literal text is deliberately not echoed.

## Swarm handoff

This does not replace:

1. concierge/bounty_qualification.py for reward/policy/saturation gates;
2. concierge/bounty_preflight.py for canonical issue/comment/assignment/competition authority;
3. concierge/work_order_lease.py for a fresh capture immediately before source mutation.

It is an additional source-contract receipt for work orders whose acceptance depends
on pre-existing repository paths or symbols. Keep the requirement list small and
issue-specific; the point is to prevent false-green dispatch without creating another
broad GitHub crawler.
