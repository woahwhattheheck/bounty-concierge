# Wavelum Core #31 — dependency-security CI handoff

Status: **acceptance conflict / do not publish as green**

Target: `stellar-network-builders/wavelum-core#31` ("Add Rust dependency vulnerability scanning with cargo-audit").

## Fresh sponsor state

- Issue is OPEN, unassigned, and carries GrantFox OSS / Maybe Rewarded / Official Campaign | FWC26.
- Current workflow preimage: `.github/workflows/test.yml` blob `ddf436b28e6d6a4f01aadb367cd8cfcbebaf0d43`.
- Current `Cargo.toml` blob: `a320ea9830ca533d143a0a908fc9fdb6f30e9b27`.
- Current `Cargo.lock` blob: `788a00307ad7c3fa0114d8cbadf3599ab822bacd`.
- No installed `woahwhattheheck` or `tokenjunkielabs` fork was visible to this seat.

## Why a direct implementation is not truthfully merge-ready

The issue requires `cargo-deny` with duplicate versions denied and acceptance says **no duplicate crate versions**.

Static analysis of the current lockfile finds 15 duplicate crate families overall. Restricting the analysis to the current production-oriented Soroban/Wasm seed plus build dependencies still reaches 8 duplicate families:

- darling 0.20.11 / 0.23.0
- darling_core 0.20.11 / 0.23.0
- darling_macro 0.20.11 / 0.23.0
- hashbrown 0.12.3 / 0.13.2 / 0.17.0
- indexmap 1.9.3 / 2.14.0
- schemars 0.8.22 / 0.9.0 / 1.2.1
- stellar-strkey 0.0.13 / 0.0.16
- syn 1.0.109 / 2.0.117

Therefore a truthful `multiple-versions = "deny"` gate is expected to fail on the current dependency stack. Adding `skip` exceptions would make the workflow green but contradict the issue's explicit "No duplicate crate versions" acceptance criterion, so this packet does **not** do that.

There is a second license-policy decision: the repository has no root LICENSE / LICENSE-MIT / LICENSE-APACHE file, and three workspace members (`vesting_vault`, `deposit_to_yield_adapter`, `insurance_treasury`) omit both `publish = false` and license metadata. Four sibling contract crates already set `publish = false`. Do not invent a license expression. A maintainer must either confirm those crates are unpublished/private (then mark them `publish = false`) or add truthful project licensing.

## Draft implementation

`draft/` contains a focused CI design that matches the requested mechanics:

1. `.cargo/audit.toml` sets RustSec severity threshold to `high`, so high/critical advisories block.
2. `.cargo/deny.toml` targets `wasm32v1-none`, excludes dev-only graph roots, allows common permissive licenses while rejecting unlisted GPL/AGPL-family licenses by default, and sets `multiple-versions = "deny"`.
3. `.github/workflows/security-audit.yml` runs cargo-audit + cargo-deny licenses + cargo-deny bans, preserves the audit JSON artifact even on failure, then fails the job if any gate failed.

This draft is intentionally **not** described as CI-green until the duplicate-version and workspace-license acceptance conflicts are resolved.

## Minimal next decision

Preferred: resolve / upgrade the transitive Soroban dependency stack until the 8 production/build duplicate families converge, and explicitly settle workspace publication/licensing. Then apply the draft workflow/config and run only the requested security job.

Fallback, only if the sponsor changes the acceptance rule: baseline the current duplicate list with explicit, temporary `cargo-deny` skips and keep `multiple-versions = "deny"` for new duplicates. That is a policy relaxation and must not be represented as satisfying the current issue text.

## Validation boundary

No sponsor source mutation, upstream PR, claim, payment action, or Michael contact was performed. No runtime `cargo deny` / `cargo audit` execution is claimed. Evidence is current GitHub source + lockfile/manifests + static dependency traversal.
