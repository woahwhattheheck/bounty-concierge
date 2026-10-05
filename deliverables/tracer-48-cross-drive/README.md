# TypeScript trace cross-drive fix

Source issue: https://github.com/tsperf/tracer/issues/48

Upstream base observed: `tsperf/tracer` main `d0af6ab262d67e9b1f7c4a7688ea4d4ffce28c2a`.
Original `src/commands.ts` Git blob: `57b522bce07e8b39057e982b61edad0163ad4743`.

## Change

Apply `tracer-48.patch` to the upstream source tree. It changes three files, with 88 insertions and 12 deletions; four focused regression cases are included.

The compiler now runs in the workspace directory for command-palette tracing and the selected directory for explorer tracing. It does not use `projectPath`, which `appState.getProjectPath()` constructs under extension global storage. The inline shell `cd` is removed. Windows uses the default shell and double-quoted trace output paths; POSIX keeps the configured shell, escapes apostrophes, and preserves literal dollar sequences during placeholder substitution.

Patch SHA-256: `dd0c6253bd4e4a7d0f86f342571f9bd51615557bad14bb1e1e76c7a17a85943a`.

Expected patched Git blobs:

- `src/commands.ts`: `66c28d945339d7c355b15e11b9d4980591aad459`
- `src/traceInvocation.ts`: `1fc8741169f9af827c3d483cc9866a7d3ed65a54`
- `test/traceInvocation.test.ts`: `b2cf096b75e11892ddbc926978f6a0e3d173dfe6`

## Validation actually executed

Node 22.16.0, isolated Linux environment:

- Reconstructed original command source matched its Git blob before editing.
- Four authored regression cases passed after TypeScript transpilation, using Node's built-in test runner in place of the unavailable Vitest package. This is not a claim that Vitest itself ran.
- The registered command reproduced the baseline storage-directory cwd and unset shell. Mocked Windows child-process integration then passed for both command-palette and explorer invocation.
- A real POSIX shell child received the correct working directory and an unchanged trace path containing spaces, an apostrophe and `$&`.
- TypeScript transpilation reported no syntax diagnostics in the three changed files. This is not full project typechecking.
- `git apply --check` passed on the original source snapshot; applying the patch reproduced all three tested files byte-for-byte.

Not run: native Windows execution, full-project lint/build/typecheck, hosted CI, or Vitest itself. Once dependencies are available, the focused upstream command is `pnpm exec vitest run test/traceInvocation.test.ts`.

## Publication state

This is a source carrier, not an upstream submission or payout receipt. The existing shared fork request at https://github.com/woahwhattheheck/commons-ship-enforcer/issues/1542 returned `publisher_rejected`. Neither `woahwhattheheck/tracer` nor `tokenjunkielabs/tracer` was accessible at the fork precheck.

Before opening a maintainer PR, refresh main and open PRs, obtain the shared writable fork, apply this exact patch, and use a technical title/body. Existing external PR #77 is open, but still chooses extension storage as the default cwd; this packet retains the workspace behavior as well as fixing cross-drive launch. Do not create duplicate internal source carriers or record this issue as a separately funded payout.
