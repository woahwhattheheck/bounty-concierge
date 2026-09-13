# Payoff-path prerequisite for `concierge claim`

A live `concierge claim` now requires an owner-reviewed payoff-path bundle before it spends provider reads or emits claim instructions. The bundle proves only that the exact bounty has a current evidence-backed compensation path and that a bounded speculative-work budget remains. It does **not** claim the issue, submit work, contact a sponsor, authorize spend, prove an award, prove payment, or recognize revenue.

## Bundle layout

Pass an existing directory whose every path component is opened without following symlinks. The directory must contain exactly named artifacts produced by `concierge.payoff_path_gate`:

```text
claim-42/
├── work.json
├── packet.json
├── review.md
└── receipt.json
```

The matching work item must:

- use `BOUNTY` with conversion event `SUBMIT_WORK`;
- have state `READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW` at live verification time;
- retain a positive `free_work_remaining_minutes` balance;
- use the exact canonical source URL `https://github.com/<owner>/<repo>/issues/<number>`;
- be the only work item in the packet bound to that target.

Repository identity is compared case-insensitively because GitHub repository paths are case-insensitive. The issue number must match exactly. Query strings, fragments, alternate hosts, pull-request URLs, and noncanonical paths do not bind.

## Compile and review

Create `claim-42/work.json` using the `payoff-path-work/v1` schema, then compile the content-addressed bundle:

```bash
python -m concierge.payoff_path_gate compile \
  --input claim-42/work.json \
  --packet claim-42/packet.json \
  --markdown claim-42/review.md \
  --receipt claim-42/receipt.json
```

Read `review.md`. `READY` means owner review only; it does not authorize any external action. If the source evidence, conversion deadline, document, packet, Markdown, or receipt changes, compile a new exclusive bundle rather than editing generated artifacts.

## Live claim

```bash
concierge claim \
  --repo acme/widget \
  --issue 42 \
  --wallet alice \
  --payoff-bundle claim-42
```

The installed entry point evaluates in this order:

1. pin every bundle-directory component and read the four members through bounded `O_NOFOLLOW` regular-file descriptors;
2. reverify exact document/packet/Markdown/receipt hashes and current temporal validity;
3. bind one READY `BOUNTY` item to the exact GitHub issue;
4. run canonical live bounty qualification;
5. run maintainer availability/terminal-outcome checks;
6. emit the existing claim instructions only when every prerequisite clears.

`--payoff-bundle` is consumed by the installed wrapper before the existing CLI parser runs. `claim --dry-run` and `claim --help` intentionally skip bundle reads and network authorities. A live claim without exactly one `--payoff-bundle DIR` fails closed. Platforms without descriptor-relative, non-symlink custody also fail closed; there is no pathname fallback.

## Failure boundary

Machine-readable failures expose only a fixed error class and reason code, for example:

```json
{"error":"claim_payoff_unavailable","reason_code":"CLAIM_TARGET_NOT_IN_BUNDLE"}
```

Raw source evidence, packet contents, local paths, comments, user identities, secrets, and provider response bodies are not copied into the JSON error boundary.
