# Bounty contract review hardening

This supplement records the evidence-integrity boundaries added after exact-head
review of the bounty contract guard.

## Content-bound comment generations

GitHub issue-comment `updated_at` values alone are not a sufficient generation
marker because their precision can collapse two authority-relevant edits into
the same timestamp. Each private double-read marker now hashes the complete
normalized representation of every returned comment: stable comment identity,
all returned stable user identity fields, author association, creation/update
timestamps, and the comment body digest. A same-timestamp edit therefore emits
`LIVE_GENERATION_UNSTABLE` and no receipt or `UNCHANGED` result.

External contributor text remains absent from the durable contract receipt. It
is included only in the ephemeral generation marker so a moving or incomplete
read cannot be mistaken for a stable snapshot.

## Create-exclusive artifacts

`--output` creates a new regular file with exclusive creation and private mode
`0600`. Existing paths and symlinks are refused; commands never truncate a
claim-time receipt or follow a symlink into another file. Operators should use a
new path for each capture and verification artifact and retain the complete
chain of evidence.

## Exact authority metadata

Receipt authority metadata is validated with an exact key set, exact values,
and exact Python value types before digest normalization. In particular,
numeric `0` cannot impersonate JSON `false`. The SHA-256 envelope remains a
consistency check—not a signature, signer identity, or trusted timestamp.
