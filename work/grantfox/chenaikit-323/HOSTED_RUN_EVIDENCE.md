# Chenaikit #323 — hosted cache observation supplement

Operation: `GFOX3-20260919-chenaikit-323/R-hosted-observation`  
Worker: ZZ-Sol-Alder-74 · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream main: `2653a3f1d670828068c3c3692d2c7834d2e8bf15`

This packet is an additive supplement to the already-merged source/readiness
baseline in PR #333. It does not replace that packet and does not authorize
upstream implementation. The source pin, workflow blobs, issue state, and
provider assignment fence remain unchanged.

## Why this supplement exists

Issue #323 explicitly asks contributors to measure current dependency install
time and to prove clean cache miss / cache hit / invalidation behavior. The
source baseline correctly avoided inventing hosted timings. Public GitHub
Actions logs now provide concrete current observations, but they also show why
the acceptance contract is **not yet satisfied**.

## Exact pinned-main pnpm miss observation

Backend CI run:

- workflow run: https://github.com/nexoraorg/chenaikit/actions/runs/35115123373
- workflow sequence: Backend CI #392
- event: push
- exact commit: `2653a3f1d670828068c3c3692d2c7834d2e8bf15`
- workflow blob: `9232e4c9e3a24c81ac87c0109f999bbf065ab972`
- run duration shown by workflow list: 26s
- backend job `104858473993`: 22s
- shared-packages job `104858474388`: 22s

Both jobs configured `actions/setup-node@v4` with Node 22 and
`cache: pnpm`. In both jobs, setup-node emitted:

`pnpm cache is not found`

The backend install step began at `2026-09-16T15:24:38.6708423Z` and pnpm
reported:

`Done in 8.3s using pnpm v9.15.9`

The shared-packages install step began at
`2026-09-16T15:24:37.6543723Z` and pnpm reported:

`Done in 7s using pnpm v9.15.9`

Both logs also report the actual project runtime selected by setup-node as
`node: v22.23.2`. The separate Actions warning that several JavaScript
actions are being forced onto GitHub's Node 24 action runtime must not be
misreported as the repository's project Node version.

### Acceptance implication

The shared-packages job succeeded after its cold pnpm-store miss. The backend
job did **not**: it failed its later Lint step on existing unused-variable
errors. Therefore run 35115123373 is legitimate cold-cache timing evidence, but
it is **not** proof that a full backend clean-miss build passes.

No warm-cache comparator on the same source/workflow revision is claimed here.
The assigned implementation still needs a controlled miss → hit pair and a
lockfile-change invalidation witness.

## Cargo miss observation on latest relevant main run

The pinned upstream head is frontend-only and did not trigger Contracts CI. The
latest relevant main Contracts run visible before it is:

- workflow run: https://github.com/nexoraorg/chenaikit/actions/runs/35109371797
- workflow sequence: Contracts CI #30
- event: push
- commit: `cd4d5ab15f5a3d6da65bdce70d5b84ab0c1c0e1b`
- workflow blob: `c2408222213394473efa49b8641ff97b13757e9b`
- total duration: 1m03s
- job `104838766926`: 58s
- conclusion: failure

The rendered cache input key was:

`cargo-Linux-e02edc50ccf9a27d9f771a6a297fa194b767c4f541c40fec36341ab14943718c`

At `2026-09-16T14:33:57.5203354Z`, Actions emitted:

`Cache not found for input keys: cargo-Linux-e02edc50... , cargo-Linux-`

The subsequent `cargo clippy --workspace --all-targets` step began at
`2026-09-16T14:34:00.0307965Z`. The log immediately shows crates being
downloaded and compiled before the job exits 101. Because the workflow has no
standalone dependency-install step, these logs do **not** support a clean
dependency-download-only duration number distinct from compilation.

### Acceptance implication

This is a real Cargo cache miss, but the workflow did not complete the required
fmt/clippy/test/wasm sequence successfully. It therefore does not prove the
issue's "cache miss still produces a clean build" acceptance criterion.

## Stronger evidence: the restore prefix actually crosses exact keys

Older main Contracts runs demonstrate that the current broad restore prefix is
not merely a theoretical concern.

Contracts CI #25:

- run: https://github.com/nexoraorg/chenaikit/actions/runs/33362300082
- job: `99395875530`
- requested exact key:
  `cargo-Linux-c4248faf11996cce9ecb0671a74f8dda770d6de2f01a6cfedc27ecced4e29254`
- restored fallback key:
  `cargo-Linux-0586edde93bffb421fcb02feebeef9216fbf7a991dd7fe76dfb23c3414fc17c4`
- restored cache size: approximately 569 MB (596,649,882 bytes)
- workflow conclusion: failure

Contracts CI #24:

- run: https://github.com/nexoraorg/chenaikit/actions/runs/33362276058
- job: `99395798738`
- requested the same `c4248f...` exact key
- restored the same older `0586ed...` fallback key
- restored approximately 569 MB
- run was cancelled after concurrency pressure and also carried a failing job

The workflow's cached payload includes `contracts/target`, not just
`~/.cargo/registry` and `~/.cargo/git`. These logs therefore prove that an
exact-key change can still restore a large older cache through
`restore-keys: cargo-${{ runner.os }}-`, including compiled target output.
That is precisely why target-output caching needs either removal from this
dependency-cache lane or a much stronger compiler/target/profile/source
identity with no incompatible broad fallback.

## Assignment-authorized proof contract

After provider/maintainer assignment, do not claim #323 complete until one
controlled workflow revision demonstrates all of:

1. pnpm cold miss with affected jobs green;
2. pnpm second-run hit on the same lockfile/toolchain;
3. `pnpm-lock.yaml` change invalidates the exact key;
4. Cargo cold miss with fmt/clippy/test/wasm green;
5. Cargo exact hit on unchanged lockfile + exact Rust identity;
6. `contracts/Cargo.lock` change invalidates the exact key;
7. Rust/toolchain/wasm-target input change invalidates the exact key;
8. no broad restore path can rehydrate incompatible `contracts/target` bytes;
9. no credentials, auth files, `.env`, `node_modules`, or generated artifacts
   enter the dependency cache;
10. timing comparison cites exact run IDs and job/step evidence.

## Authority fence

- upstream source mutated by this supplement: **false**
- GrantFox application submitted by this supplement: **false**
- assignment claimed: **false**
- reward / award / payment claimed: **false**
- implementation before assignment: **forbidden by the upstream issue contract**
