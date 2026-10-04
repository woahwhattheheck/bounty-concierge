# Loop accumulator detector validation

## Scope

The submitted patch changes nine source/documentation files against upstream main `9f6f9e4302f1982e044ab6d308782bfd9fb03255`. No submitted Cargo manifest or lockfile changes are present. This work is associated with GrantFox OSS issue #680, labelled **Maybe Rewarded**; no fixed award or payout is asserted. Maintainer assignment and compensation remain pending; one earlier generic assignment request is present.

## Observed execution

1. The original locked, nondefault-feature core snapshot command was attempted on Rust 1.85.0. Its `stellar-xdr` 20.1.0 compiler process received SIGKILL (signal 9), before `sanctifier-core` or any test ran. The shared container had an 8 GiB cgroup memory limit and recorded OOM kills; the per-process cause is inferred from the SIGKILL and cgroup evidence.
2. A temporary workspace retained the complete actual core sources, parser, rule registry, fixture/tests, benches, and documentation through symlinks. Only temporary workspace membership and the unused `soroban-sdk` dependency edge were changed. Static inspection found SDK names only in source-text fixtures and a description string. The optional Z3 declaration remained; execution used `--no-default-features`.
3. Offline resolution pruned unreachable dependencies. All 100 retained registry package name/version/source/checksum tuples match the original lockfile. A subsequent `cargo metadata --offline --locked` produced the same graph.
4. The real core compiled in 1m 22s and produced the required insta snapshot. All pre-snapshot assertions passed: exactly six intended function locations, the registered finding code, and equality between direct execution and the actual default registry. The pending snapshot was inspected and accepted. The rerun passed **1 test** in 0.26s.
5. Existing documentation coverage passed **3 tests** in 0.01s: every registered detector has a page, no orphan detector pages, and every detector appears in the index.
6. Formatting checks and `git diff --check` passed. Build artifacts occupied 233 MiB with one job, no debug information, and incremental compilation disabled.

The rule reported `sum_for:20`, `sum_while:29`, `sum_loop:41`, `sum_groups:52`, `sum_with_fee:63`, and `sum_in_condition:71`. Checked/saturating additions, nonaccumulating addition, fresh iteration bindings, shadowed variables, pre-loop iterator evaluation, and deferred bodies produced no findings in the fixture.

## What remains

This is focused execution of the real core under a temporary dependency manifest. It is not a passing original SDK/full-workspace build or a full CI claim. Run the original commands after publication in the sponsor environment; review/merge and any conditional award remain external decisions.

The detector is a documented syntactic heuristic for plain identifier accumulators. It does not perform integer type/range analysis, prove that any particular loop overflows, follow aliases or helper calls, or establish that an outer accumulator is not reset every iteration.

## Reproduction inputs

`lean-validation/Cargo.toml`, `lean-validation/core-Cargo.toml`, and `lean-validation/Cargo.lock` preserve the exact temporary manifests/lock used. Create a separate workspace using the first manifest; place the second at `tooling/sanctifier-core/Cargo.toml`; link the original core `src`, `tests`, and `benches` and the original root documentation into the same relative layout. Preserve any source `include_str!` paths under `tooling/zk` and `contracts`. Use the unchanged original checkout for upstream validation.

Raw command logs and the dependency preparation receipt accompany this packet. The temporary manifests belong to the validation packet only and must not be included in the upstream contribution.
