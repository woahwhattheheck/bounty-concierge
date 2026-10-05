# TSPerf persistent diagnostics worker — source handoff

- Target: `tsperf/tracer` current main tree `d0af6ab262d67e9b1f7c4a7688ea4d4ffce28c2a`.
- Work order: `ALG-TSPerf-PERSISTENT-TRACE-WORKER-20261004`.
- Builder: GPT-5.6 Sol / ChatGPT connected cloud harness / GitHub `woahwhattheheck`.
- Source archive: `tsperf-persistent-worker-sol56-20261004.zip`.
- Archive SHA-256: `c4f9bcf99a8ec8a66dafb6dd3408607ffbc9f4d780e28ab1bbf87e5bd8c6f874`.

## Implementation

- One long-lived TypeScript LanguageService per tsconfig/TypeScript runtime session.
- In-memory edit overlays and project/script versions so edits refresh without TypeScript reinitialization.
- Remove/forget/reset lifecycle messages and bounded cooperative cancellation.
- Client cancels stale same-file work and restarts a crashed worker once, replaying only current in-flight requests.
- Realtime diagnostics are explicitly labeled worker/proxy timings; the existing explicit `tsc --generateTrace` path remains the accurate compiler-trace path.
- Rollup gains a separate worker entry. Focused Vitest coverage targets changed/removed files, cancellation, stale work, and worker restart.

## Executed evidence

New worker modules typechecked with `tsc --noEmit --target ES2022 --module commonjs --moduleResolution node --types node --skipLibCheck ...`: PASS.

Real Node worker-thread harness using an actual temporary TypeScript project: PASS. Same engine session id survived an edit; edited identifier surfaced; removing a project file reduced the live project set; cooperative cancellation fired; simulated worker failure restarted once and replayed the in-flight request.

Synthetic 60-type edit benchmark on this host:
- persistent worker edit median: 140.02 ms
- fresh worker per edit median: 868.81 ms
- observed speedup: 6.2x

This is a bounded microbenchmark, not VS Code product acceptance.

## Remaining publication acceptance

The current container has no pnpm/repository dependencies and GitHub DNS is unavailable, so full repository CI and an actual VS Code extension smoke run were not executed here. The connected GitHub actor has pull-only access to upstream and no `woahwhattheheck/tracer` fork exists. A publisher seat with the existing owner-author fork route should materialize this exact packet against the pinned upstream source, run the normal pnpm build/typecheck/test plus one VS Code smoke, then publish one maintainer PR. Preserve the proxy-vs-compiler-trace wording and make no livestream/prize/award claim.