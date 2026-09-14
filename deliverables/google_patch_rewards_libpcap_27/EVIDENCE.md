# libpcap #27 hardening evidence

Operation: `PATCH-REWARDS-LIBPCAP-27-ZTWA3K7-20260914`

Owner/finalizer: **Z-TerbiumWeir-1920-A3K7 (`ZTW-A3K7`) / GPT-5.6 Sol**

Pinned upstream: `the-tcpdump-group/libpcap@f15c258be8f269875b351f4c437992194c1baa2b`

Upstream issue: https://github.com/the-tcpdump-group/libpcap/issues/27

## Finding

The historical failure is not isolated to `count_blocks()`. On the pinned upstream `optimize.c`, four control-flow-graph walkers recurse over `JT()`/`JF()` edges:

- `count_blocks()`;
- `number_blks_r()`;
- `find_levels_r()`;
- `convert_code_r()`.

A one-function rewrite therefore leaves sibling stack-exhaustion paths. The proposed patch places one iterative CFG-depth preflight at the start of `opt_init()`, before any of those recursive walks. The preflight records the greatest path depth observed for a shared DAG node so a shallow first visit cannot hide a deeper later path; an unexpected cycle also grows until the bound and fails closed.

The chosen bound is 128 CFG blocks. This is intentionally conservative because `convert_code_r()` has a larger frame than `count_blocks()` and the security property is to reject pathological compiler input before consuming an unbounded C stack. Normal filters remain far below the bound. The error is explicit: `filter expression is too deeply nested to optimize`.

## Local faithful-harness proof

`regression_harness.c` contains the exact recursive shape of upstream `count_blocks()` plus the proposed iterative preflight logic and synthetic CFGs. It compiles with:

```sh
cc -std=c11 -O2 -Wall -Wextra -Werror -pedantic regression_harness.c -o regression_harness
./regression_harness
```

Observed results:

```text
unpatched_low_stack_crashes: PASS
guard_rejects_deep: PASS
guard_accepts_boundary: PASS
guard_tracks_deeper_revisit: PASS
guard_cycle_fail_closed: PASS
```

The first check forks a child, constrains its stack to 64 KiB, builds a 50,000-block chain, and confirms the unpatched recursive shape terminates by `SIGSEGV`/`SIGBUS`. The patched preflight rejects the same chain without entering recursive traversal. Boundary depth 128 is accepted, a shared node reached first at depth 3 and later at depth 5 is correctly judged by the deeper path, and a cycle fails closed.

Frozen local hashes at evidence capture:

- harness source SHA-256: `ec4f65acf76e10a3de44fdde12d64e3e1bfe5a709582d993c64cf6d00a4c5a26`
- harness binary SHA-256: `4ec1bd25b158a3ebffcb7dd42acd5251d3d8eb075324b8dec21942ea7897d72b`
- patch SHA-256: `79ae4b6c9ab37a6244ba8357e1b3fbcf79d5e9bc09fa1a75354d547763bc5fef`

## Hosted proof contract

The carrier includes `.github/workflows/libpcap-27-patch-proof.yml`. On the PR it clones exactly the pinned upstream commit, applies `libpcap-27-stack-depth-guard.patch`, builds upstream `filtertest` with CMake, proves ordinary filter compilation succeeds, then feeds a generated >128-deep conjunction and requires a clean compiler rejection containing `too deeply nested to optimize` rather than successful compilation or a signal crash.

Hosted CI results are not claimed until a workflow run exists and is inspected.

## Reward truth

`libpcap` is in Google Patch Rewards Tier 1 / core infrastructure data parsers. That makes an upstream-merged proactive security hardening patch potentially rewardable under the program rules; this carrier does **not** assert eligibility, award amount, payment, or revenue. Those exist only after upstream merge and Google adjudication.
