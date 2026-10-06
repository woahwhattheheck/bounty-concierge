# warpSpeed public intake

`tools/warpspeed_intake.py` converts the public
`warpspeedopen-source/warpspeed-bounties/BOUNTIES.md` table into a compact,
sanitized fleet lead list without using GitHub API quota.

## Usage

Fetch the public board once:

```bash
python tools/warpspeed_intake.py --fetch --output warpspeed-public.json
```

Or retain/replay an already captured board without network access:

```bash
python tools/warpspeed_intake.py \
  --input BOUNTIES.md \
  --retrieved-at 2026-10-06T18:55:00Z \
  --output warpspeed-public.json
```

The default floor is $15. Override it with `--minimum-usd`.

## Dispatch contract

The tool deliberately outputs **leads, not source leases**.

- Rows whose public board status is not Open, whose reward is below the floor, or
  whose signup capacity is 100% are pruned.
- Remaining rows are emitted with `dispatch_status=CLAIM_GATE` and
  `build_allowed=false`.
- Advertised dollars remain `provider_funding_status=ADVERTISED` and
  `sponsor_verification=UNVERIFIED`; the parser never calls them escrowed,
  awarded, invoiced, or paid.
- Before source work, a seat must reconcile the current official bounty page,
  canonical GitHub issue/comments/PRs, fleet ownership, account eligibility, and
  current sponsor/payment policy.
- warpSpeed's published process requires developer signup, a GitHub claim
  request, and **maintainer confirmation before paid work begins**. This tool
  performs none of those mutations.
- Board timeline text is preserved for review but is not silently promoted into
  current eligibility when the same public board still labels a row Open.

This keeps public discovery off the authenticated GitHub search rail during rate
limits while preserving the commercial and collision fences needed before work.
