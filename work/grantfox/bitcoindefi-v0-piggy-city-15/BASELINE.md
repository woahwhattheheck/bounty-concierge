# GrantFox baseline — Bitcoindefi/v0-piggy-city #15

Operation: `GFOX2-20260919-017-R-ZZ-SOL-DELTA`  
Worker: ZZ-Sol-Delta · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `1846b4f204974654050beecf56a56391dc7ad138`

## Canonical issue

- GitHub: https://github.com/Bitcoindefi/v0-piggy-city/issues/15
- GrantFox: https://contribute.grantfox.xyz/org/Bitcoindefi/repo/v0-piggy-city/issue/15
- State at observation: OPEN
- GitHub assignee: none
- GrantFox public assignment state: Unassigned
- GrantFox route: `Apply to this issue` visible; one application per user; direct GitHub comment
- Existing application comments at observation: 2, from other users
- Labels: `godot-port`, `Maybe Rewarded`, `GrantFox OSS`, `Third Campaign`
- Reward interpretation: eligibility/campaign metadata only. No fixed award, approval, or payment is asserted.
- Upstream connector permission snapshot: pull=true, push=false

This is a pre-assignment source and dependency packet. It does not implement the upstream issue.

## Current-source correction

Issue #15 says the game is currently deployed as Next.js and asks to preserve a Web/HTML5 target in Godot. The pinned repository has already moved beyond that wording in one important way: it ships a compiled Godot Web/WASM game under `public/game/`.

Relevant landed history:

- `f3264aed5a076133b2d09f8a080960e219135bba` — added a standalone Godot 4.7 Web/WASM build under `public/game/`; commit text records a single-threaded WebGL2 build and browser verification.
- `37e337c0302a985ba85224c9f5e72cb87243bb6a` — repaired `/game` vs `/game/` asset resolution by adding `<base href="/game/">`; commit text records headless-browser verification.
- `1846b4f204974654050beecf56a56391dc7ad138` — merged that base-href repair and is the pinned default-branch head observed for this packet.

The current `public/game/index.html` blob is `2f7c9a63af5106f3e7e75c76aca9f8fa77284387`. Its embedded Godot config reports:

- `index.pck`: 4,382,168 bytes
- `index.wasm`: 39,509,339 bytes
- threads disabled
- a splash/progress/notice boot overlay already present
- the `/game/` base-href fix already present

So the residual is not “make any browser build.” It is to make the existing browser target reproducible and maintainable from source while preserving the known-good subpath behavior.

## Reproducibility gap on pinned main

Exact-path reads on the pinned default branch did not find:

- `project.godot`
- `export_presets.cfg`

Repository code search did not surface a `godot --headless` build path. The current `README.md` (blob `bc9ef39725e5acfeb108ec6a9538d8be0f3bda2c`) still documents only the Next.js development flow and automatic deployment behavior.

That means the compiled `public/game/` artifacts are not, on this tree, backed by the source-level export contract requested by #15.

## Blocking upstream dependency: issue #4

Issue #4 — “[Godot port] 01 — Project scaffold + estructura base” — explicitly says it is the base and **blocks the rest**. It requires:

- a Godot 4.x project,
- `scenes/`, `scripts/`, `assets/`, `autoloads/`,
- `project.godot`,
- a minimal runnable scene,
- Godot-oriented README instructions.

At observation, #4 is still OPEN, unassigned, has zero comments, and no open PR carrier surfaced. The exact `project.godot` path is still absent from pinned main.

Therefore a correct #15 implementation plan should preserve the dependency boundary: application/source planning is valid now, but assignment-dependent implementation should either consume an accepted #4 scaffold or explicitly coordinate a maintainer-approved combined dependency path rather than silently rebuilding the project foundation inside #15.

## Existing loading shell: preserve, then customize intentionally

The checked-in Web shell already has a generic Godot splash image, progress element, failure notice, and the critical `<base href="/game/">` compatibility fix.

A #15 implementation should not replace that shell blindly. The source-level export configuration should make the custom loading UX an intentional template/preset input and prove that regeneration does not lose the `/game/` routing fix.

## Narrow implementation plan after assignment/dependency clearance

1. Consume the canonical Godot project scaffold from #4 (or a maintainer-approved equivalent) and pin the Godot/export-template version.
2. Add a tracked `export_presets.cfg` Web preset whose output is the existing `public/game/` route.
3. Keep the browser target single-threaded unless the project deliberately adopts the COOP/COEP requirements needed for threads.
4. Promote the current shell behavior into a tracked custom HTML/loading template; preserve the `/game/` base-href behavior and visible failure state.
5. Add a deterministic headless export command matching the issue acceptance contract: `godot --headless --export-release "Web"`.
6. Run the export from a clean checkout with the pinned export templates; fail if generated JS/WASM/PCK/index outputs are missing.
7. Add a browser smoke check that serves the generated directory over HTTP and verifies the page, JS, WASM, PCK, canvas boot, and no fatal console/startup failure.
8. Record generated artifact byte sizes and compare them to a documented baseline so “size optimization” is measurable rather than aspirational.
9. Document editor testing, export-template installation, headless build, local HTTP serving, CI/deploy behavior, and the `/game/` subpath constraint in README.
10. Capture the requested browser evidence with the address bar visible after the implementation is runnable.

## Acceptance traps to test

- Regenerating the export must not drop `<base href="/game/">` and reintroduce root-relative 404s.
- Opening `/game`, `/game/`, and `/game/index.html` should resolve the same JS/WASM/PCK assets.
- A missing or wrong export template version should fail visibly, not reuse stale checked-in artifacts.
- Browser smoke must run via HTTP, not `file://`, so WASM/MIME/subpath behavior is exercised.
- The test should distinguish a page that loads HTML from an engine that actually reaches canvas/game boot.
- Size checks should compare named artifacts and report deltas; they should not fail because of nondeterministic timestamps alone.
- Custom loading/error UI must remain usable when engine feature detection fails.

## Application draft / provider attempt

A provider application automation was launched from this session using the source-specific plan below. This file intentionally does **not** claim submission success until the provider run reaches a terminal state and is read back.

> I checked current main at `1846b4f204974654050beecf56a56391dc7ad138` before applying. The repository already ships a working Godot 4.7 Web/WASM bundle under `public/game/` (including the `/game` base-href boot fix), but current main does not contain `project.godot` or `export_presets.cfg` and the README still documents only the Next.js development path.
>
> After assignment, I would make the existing web target reproducible from source rather than replacing it: add/consume the Godot project scaffold and Web export preset, preserve `/game/` asset resolution in a tracked custom loading template, measure and document build-size changes, add a browser smoke check for index/JS/WASM/PCK boot, and document editor + CI/deploy steps.
>
> I would verify `godot --headless --export-release "Web"` from a clean checkout, serve the generated build locally over HTTP, and include browser evidence with the address bar visible. I also found that issue #4 explicitly blocks the rest of the Godot port and is still open, so I would coordinate that dependency rather than smuggling the scaffold into #15 without maintainer agreement. I will wait for official assignment before assignment-dependent implementation.

## Verification performed for this packet

- Read GitHub issue #15 and both existing comments.
- Read live GrantFox listing: OPEN / Unassigned / application route visible.
- Read upstream permission snapshot: pull=true, push=false.
- Pinned current default-branch head.
- Read current README and `public/game/index.html` blobs.
- Verified `project.godot` and `export_presets.cfg` are absent at their canonical root paths.
- Searched default-branch code for a headless Godot build path; none surfaced.
- Read the two landed Godot web-build commits and their stated browser verification.
- Read blocking issue #4 and confirmed it remains open/unassigned with no comments or open PR carrier surfaced.
- Reviewed open PR census; current open PR #29 is an unrelated NFT contract carrier.

## Authority boundary

No upstream source, issue assignment, wallet, funds, reward, approval, or payment is mutated by this artifact. Upstream implementation remains assignment/dependency gated. The owned `woahwhattheheck/bounty-concierge` repository is used only to preserve the source census and application packet.
