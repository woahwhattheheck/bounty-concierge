# Maintainer contribution-pause gate

The live bounty preflight treats canonical GitHub maintainer instructions as
dispatch authority when they explicitly pause new contribution work.

## Authority boundary

Only issue text or comments whose GitHub `author_association` is
`OWNER`, `MEMBER`, or `COLLABORATOR` can produce a pause signal. External
commenters cannot suppress a bounty by posting stop language.

The classifier is intentionally narrow. It recognizes explicit instructions
such as “hold off with any attempts”, “no more claims”, “do not submit a PR for
this bounty”, or “stop new submissions”. Generic discussion such as “hold this
issue open” does not match.

## Dispatch semantics

A current pause produces:

- disposition: `HOLD`
- dispatch: `false`
- reason: `MAINTAINER_CONTRIBUTION_PAUSED`

A pause is not a rejection: it is reversible maintainer state. The preflight
exports only a boolean/count signal and never copies the authoritative comment
text into the receipt.

Existing canonical-generation protection still applies. A pause that appears
while an otherwise-actionable preflight is running changes the bound comment
generation and forces a rerun. A pause present at the initial read remains a
conservative HOLD until the authoritative text is edited or removed and a new
preflight is run.

This gate does not grant claim, implementation, merge, submission, payment, or
sponsor-contact authority.
