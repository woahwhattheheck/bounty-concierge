# Payoff-bound live claims

`concierge claim` treats compensation evidence as a prerequisite, not as an after-the-fact note.
A live claim with a valid target must provide exactly one owner-supplied bundle:

```bash
concierge claim \
  --repo owner/repository \
  --issue 123 \
  --wallet OWNER_WALLET \
  --payoff-bundle /path/to/payoff-bundle
```

The wrapper verifies the payoff bundle **before** canonical bounty qualification or maintainer-availability provider reads. Help and `claim --dry-run` remain preview surfaces: they do not open the bundle or call those providers.

## Fixed bundle members

The directory contains these exact names:

- `work.json` — payoff work document.
- `packet.json` — compiled owner-review packet.
- `review.md` — exact compiled Markdown review.
- `receipt.json` — exact packet/Markdown receipt.
- `previous-receipt.json` — optional custody member used by non-bootstrap continuity generations when the canonical payoff verifier requires a predecessor receipt.

The bundle reader opens every directory component and member through retained POSIX descriptors with `O_NOFOLLOW`, enforces regular-file, UTF-8, size, and generation-stability checks, and fails closed when safe descriptor-relative custody is unavailable. The optional predecessor is considered absent only on a true `FileNotFoundError`; permission, symlink, and other open failures remain errors.

## Authority composition

The claim boundary does not invent or downgrade payoff semantics. It delegates exact document/packet/Markdown/receipt and continuity validation to the installed `payoff_path_gate` verifier. That includes current v3 policy/evidence continuity and its predecessor-receipt requirements.

After canonical verification succeeds, the adapter requires exactly one packet result whose canonical source is exactly:

```text
https://github.com/<owner>/<repo>/issues/<number>
```

Repository identity is case-insensitive, while issue number and URL structure must match exactly. That row must still be:

- `READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW`
- mechanism `BOUNTY`
- conversion event `SUBMIT_WORK`
- positive `free_work_remaining_minutes`

Only then may the existing bounty preflight and maintainer-availability checks run. The wrapper-only `--payoff-bundle` argument is removed before the underlying CLI parser receives argv.

## Fail-closed behavior

Missing, duplicate, malformed, stale, exhausted, ambiguous, target-mismatched, continuity-invalid, or tampered payoff evidence exits before provider reads. Machine-readable errors expose only a stable reason code, not source evidence text.

This gate is **prerequisite verification only**. It does not claim or comment on GitHub, submit work, contact a sponsor, authorize spend, mutate payment/wallet state, prove an award, prove payment, or recognize revenue.

## Recovery provenance

This implementation recovers the intent of stale PR #148 by Z-BirkhoffQuay (`ZBQ-M8V2`) after its original v1 payoff dependency was superseded. The recovery is rebuilt from current `main` and adds predecessor-receipt propagation required by chained payoff continuity rather than merging the stale branch unchanged.
