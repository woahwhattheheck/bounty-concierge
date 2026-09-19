# Ajo-contrib/soroban-ajo #900 — production-profile contract-test readiness

Captured 2026-09-19 by ZZ-SolForge / GPT-5.6 Sol.

## Assignment fence

- Ajo-contrib/soroban-ajo#900 is OPEN and unassigned with GrantFox OSS / Maybe Rewarded / Third Campaign labels.
- GrantFox public state is **Unassigned** with an Apply route visible.
- Fresh Slack census found no #900 TAKE/DONE/carrier, and GitHub search found no issue-closing implementation carrier.
- Upstream repository access is read-only for the connected account. This is pre-assignment evidence only.

## Pinned source generation

`master@1b87ea7344ae3d871e54abff05eabe5113bd2956`

| Path | Git blob | Finding |
|---|---|---|
| `contracts/ajo/Cargo.toml` | `7e22d95b03fa35e31ae3377d1e6f2c6f20fe829f` | Release profile already sets `opt-level="z"`, `overflow-checks=true`, `debug-assertions=false`, `panic="abort"`, `codegen-units=1`, `lto=true`. |
| `.github/workflows/ci.yml` | `1bf6be0a59130d76696a349c27c3b77537da13cf` | Builds Wasm with `--release`, then runs tests with plain `cargo test --locked`. |
| `.github/workflows/pr-checks.yml` | `697582999c6fd7fa9ea1fd7810e9106a8eaa7b4a` | PR contract tests are plain `cargo test --locked --verbose`. |
| `contracts/ajo/docs/SECURITY_AUDIT_REPORT.md` | `efe2c4a853ed85e5dc7b88032ec71a9dbc9367db` | Explicitly claims release overflow checks are enabled and relies on that as an arithmetic mitigation. |
| `docs/DEPLOYMENT.md` | `4f4b565c4fe1c016b3885abc069799cffa318c5c` | Deployment guidance builds the contract then tells operators to run plain `cargo test`. |

## Corrected problem statement

The issue description warns that production release/Wasm may silently disable overflow checking. **That is not true for the pinned current source:** this repository explicitly enables `overflow-checks = true` in `[profile.release]`.

The residual regression gap is still real:

- CI compiles the deployable Wasm under the release profile.
- The full test suite is then executed under the default test profile, not `--release`.
- PR checks likewise exercise only the default test profile.
- The security documentation treats release-profile overflow checking as a mitigation, but CI does not run the behavioral suite under that release configuration.

So current safety depends on a Cargo profile setting that can drift independently from the profile actually exercised by tests.

## Recommended assigned implementation

After official assignment:

1. Add a release-profile contract-test leg such as `cargo test --locked --release`, or an explicitly named profile that inherits the deployable release semantics.
2. Keep the existing fast/default test leg; this is differential coverage, not a replacement.
3. Add a small regression canary whose behavior is profile-sensitive enough to prove the release leg is actually active, without relying on undefined wraparound behavior.
4. Fail if the deployable Wasm profile and the tested production-like profile diverge on safety-critical flags (`overflow-checks`, panic strategy where test harness permits, debug assertions, optimization assumptions).
5. Update deployment/security documentation to state the intended overflow policy: current source chooses **panic-on-overflow in release** in addition to explicit checked arithmetic where present.
6. If `panic="abort"` prevents direct release test harness execution on the target/toolchain, use a dedicated test profile inheriting the production safety flags and separately verify its declared delta from `release` rather than weakening the deployed profile.

## Acceptance evidence

- exact Cargo profile diff;
- default + production-like test commands and exit codes;
- CI configuration proving both legs run;
- regression proving profile drift is detected;
- documentation aligned with the actual release policy.

This packet does not claim a fixed reward and creates no provider application, upstream source, submission, wallet, or payment authority.
