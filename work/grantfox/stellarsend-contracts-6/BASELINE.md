# GrantFox baseline — StellarSend/contracts #6

Operation: `ZZF-GFOX-DISC-007-R-ZZ-SOL-DRIFT`  
Worker: ZZ-Sol-Drift · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `d510c6e9af81df5ede14287d58ce7eca84ee6147`

## Canonical issue

- GitHub: https://github.com/StellarSend/contracts/issues/6
- GrantFox: https://contribute.grantfox.xyz/org/StellarSend/repo/contracts/issue/6
- State at observation: OPEN
- GitHub assignee: none
- GrantFox public assignment state: Unassigned
- Existing issue comments: 2 applicant comments
- Matching PR search for `InvalidPath`: no carrier surfaced
- Labels: `bug`, `contracts`, `testing`, `very hard`, `Maybe Rewarded`, `GrantFox OSS`, `Official Campaign | FWC26`
- Reward interpretation: campaign / `Maybe Rewarded` labels are eligibility signals only; no fixed award or payment is asserted.

This is a pre-assignment source census and application packet. No upstream source mutation is performed here.

## Current-source finding

The issue remains live on the pinned head.

`stellar_send/src/lib.rs` still accepts `path: Vec<Address>` in `send_path_payment`, then constructs:

- `send_token`
- every supplied intermediate hop
- `dest_token`

into `full_path` without any length, endpoint, or duplicate validation. The resulting `full_path` is not consumed by the current 1:1 simulated swap path.

Pinned blobs:

- `stellar_send/src/lib.rs`: `f502afc8441f21426931ceb22f754094a834071d`
- `stellar_send/src/error.rs`: `00db98c55436ff87efd5c9bc221029360d50c4c9`
- `stellar_send/src/test.rs`: `4c3bd7351b04a409591622c14cd393e9315c1778`
- `stellar_send/Cargo.toml`: `43b5234ba7370095cbbd183da6345a649a8bef43`

`StellarSendError::InvalidPath = 8` still exists, but no `InvalidPath` return path surfaced in current `lib.rs`, so the variant remains effectively dead for path validation.

Important issue clarification: despite the original first paragraph saying "rejects empty", the issue's own deeper notes explicitly say an empty intermediate path means a direct `send_token → dest_token` swap and should be allowed. The implementation should follow that clarified behavior rather than rejecting empty paths.

## Bounded validation contract after assignment

A focused implementation should validate before constructing/using the full route and before any state mutation or token movement.

Recommended invariant set:

1. empty intermediate path is valid;
2. `path.len()` must not exceed a small explicit `MAX_PATH_LEN`;
3. no intermediate hop may equal `send_token`;
4. no intermediate hop may equal `dest_token`;
5. no duplicate intermediate address anywhere in the bounded path;
6. every violation returns `StellarSendError::InvalidPath`.

At a maximum of 3–4 hops, all-pairs duplicate detection is simple and bounded; it avoids allowing non-consecutive loops such as `[A, B, A]`, which a consecutive-only check would miss.

The issue suggests 3–4 hops as a reasonable starting point, but the exact constant should be named/documented and chosen with maintainers rather than smuggled in as an unexplained magic number.

## Test matrix after assignment

Add focused contract tests for:

- empty path succeeds as direct route;
- one distinct intermediate hop remains accepted by validation;
- path containing `send_token` fails with `InvalidPath`;
- path containing `dest_token` fails with `InvalidPath`;
- consecutive duplicate hops fail;
- non-consecutive duplicate hops fail;
- exactly `MAX_PATH_LEN` hops passes validation;
- `MAX_PATH_LEN + 1` fails;
- invalid path fails before payment sequence/storage/balance side effects.

The current swap is still a simulation, so tests should distinguish **validation acceptance** from proof that a real DEX router consumed the route. Do not claim router forwarding until issue #2 or equivalent integration exists.

## Verification plan

After assignment:

- `cargo fmt --all -- --check`
- focused `stellar_send` test invocation from the workspace
- `cargo clippy` using the repository's supported target/features
- workspace/build command used by upstream CI

Report exact commands and exit codes, including unrelated baseline failures.

## Application draft

> Applying for StellarSend/contracts #6 after checking current `main` at `d510c6e9af81df5ede14287d58ce7eca84ee6147`.
>
> The gap is still present: `send_path_payment` builds `full_path` from the endpoints plus every supplied hop with no path validation, while `InvalidPath = 8` remains defined but unreachable for this case. I also noticed the issue's later clarification that an empty intermediate path is valid/direct, so I would preserve that rather than implementing the earlier contradictory "reject empty" wording.
>
> After assignment I would add one bounded validation helper before route construction/state/token effects: allow empty, cap the hop count with an explicit documented constant, reject endpoint tokens as intermediates, and reject any repeated intermediate address (including non-consecutive loops). I would add boundary and side-effect tests for empty, valid, endpoint-overlap, duplicate, MAX, and MAX+1 cases, then run fmt/clippy/tests/build and report exact results.
>
> I will keep the change focused on making `InvalidPath` meaningful and bounding future router work; I will not claim the current 1:1 simulation forwards a real route.

## Authority boundary

The GitHub connector has `pull=true`, `push=false` on the upstream repository; the collaborator-permission endpoint returned GitHub 403 `Resource not accessible by integration`, which is a connector limitation rather than proof about the human account.

No upstream code, provider assignment, issue state, wallet, reward, or payment is changed by this artifact.
