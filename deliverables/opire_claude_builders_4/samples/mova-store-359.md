# Sample review - Movalabs-crew/mova-store#359

Input PR: https://github.com/Movalabs-crew/mova-store/pull/359

## Summary
This PR strengthens the contracts CI job by installing `rustfmt` and `clippy`, then running formatting and lint checks before the existing contract tests. It also applies small Rust source changes consistent with lint/format cleanup in token-transfer calls and persistent-storage formatting.

## Identified risks
- `cargo clippy --all-targets -- -D warnings` turns every existing warning in every target into a hard CI failure, so unrelated pre-existing warnings can block future changes unless the repository intends that policy.
- The `MuxedAddress::from(...)` call sites change from borrowed temporaries to owned values; that is likely a lint/API cleanup, but it is a semantic type-level edit and should be reviewed separately from pure formatting.
- Adding `rustfmt` and `clippy` to the pinned toolchain is necessary for the new commands, but it also makes those components part of CI availability and version stability for this job.

## Improvement suggestions
- Keep the CI-policy change and the lint-driven Rust source edits clearly separated in commit history or PR explanation so future reviewers can distinguish enforcement from behavior changes.
- If the repository intentionally wants a zero-warning policy across all targets, document that policy beside the command so later maintainers do not weaken `-D warnings` merely to unblock an unrelated PR.

## Confidence
High

---
Reviewed: [Movalabs-crew/mova-store#359](https://github.com/Movalabs-crew/mova-store/pull/359)
