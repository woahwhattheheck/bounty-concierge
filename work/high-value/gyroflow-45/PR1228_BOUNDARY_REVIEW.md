# Gyroflow #45 — PR #1228 residual-boundary acceptance review

Status: exact-head donor/review packet; **not** a competing optical-only implementation, `/attempt`, `/claim`, award, or payout receipt.

## Pins

- Canonical issue: `gyroflow/gyroflow#45` — “Optical only stabilization”.
- Posted sponsor amount observed in the canonical issue thread: **$500 USD via Algora**. This remains a competitive sponsor offer, not earned revenue.
- Upstream literal `master`: `d918ab3594e539f25a67a9f1d2b8e042798f61f7`.
- Reviewed carrier: `gyroflow/gyroflow#1228`, exact head `735df5a8b00261b2f88e0cde387f3088daf5f43a`.
- At review time #1228 was OPEN, non-draft, mergeable, based on the same literal `master`.
- #1228 intentionally omits `/claim #45` pending a real-footage demo; its CLA bot also reported the contributor signature as pending at readback.
- Other mature #45 carriers already exist (#1204, #1215, #1200, #1189, etc.), so the useful contribution here is an exact acceptance defect, not another whole-feature implementation.

## Blocking defect: rejected/missing optical pairs erase the segment boundary

The residual pipeline has a discontinuity bug that can carry a pre-cut / pre-failure warp into later frames.

In `src/core/synchronization/optical_stab.rs`, `build_from_estimator()` walks estimator results and silently skips several kinds of discontinuity:

- non-consecutive frame numbers: `if curr.frame_no + 1 != next.frame_no { continue; }`
- unavailable / borrow-failed optical flow
- zero optical-flow size
- `robust_similarity()` rejection
- `grid_from_residuals()` rejection

Only successful pairs are appended to `increments` and `timestamps`. There is no boundary marker or segment identity for any skipped pair.

The next stage, `stabilize_grids()`, consumes only that compressed vector. `accumulate()` and `box_smooth()` therefore treat the last valid sample before the gap and the first valid sample after the gap as adjacent in time. A scene cut for which tracking fails outright is especially important: because no grid is emitted, `is_cut()` never sees a large residual and never resets the path.

The final stage compounds this. `meshes_from_corrections()` maps the sparse timestamps back to video frames and deliberately holds the previous table:

```rust
let mut last: Option<&Vec<(f64, f64)>> = None;
...
if let Some(g) = incoming {
    last = Some(g);
}
let Some(g) = last else {
    meshes.frames.push(MeshFrame::default());
    continue;
};
...
if let Some(idx) = last_idx {
    meshes.frames.push(MeshFrame {
        table: Some(idx),
        ...
    });
    continue;
}
```

So if a valid residual exists at frame 0, tracking/correlation is rejected for frames 1–9, and a new valid segment begins at frame 10, frames 1–9 reuse frame 0's residual table. In the scene-cut case, that can put the old scene's local warp onto the new scene until tracking recovers.

This contradicts the carrier's stated failure/cut behavior (“fail-closed” / scene-cut handling) and can visibly distort frames exactly when optical tracking is least trustworthy.

## Why the current regression does not cover it

The existing `scene_cut_resets_path` test injects a giant residual grid directly into `stabilize_grids()`. That proves the downstream `is_cut(grid)` branch when a cut survives all upstream filters.

It does **not** exercise the common pipeline path where the cut or decode/tracking failure is rejected before a grid is emitted. That path is represented by absence, and absence is currently erased.

The existing mesh interning/coverage tests also affirm hold-last behavior between sparse samples, but do not distinguish an ordinary sampling interval from an explicit tracking/cut boundary.

## Required repair contract

Preserve discontinuities as first-class data rather than compressing them away. Any of these designs can work:

1. carry a segment id beside each valid increment/timestamp;
2. keep a frame-aligned `Option<Grid>`/boundary stream;
3. emit explicit boundary records when frame adjacency or optical validation fails.

Then enforce:

- path accumulation resets at every boundary;
- temporal smoothing never reads samples from another segment;
- a boundary/gap produces identity/default residual frames until the new segment has its own valid correction;
- hold-last is allowed only *inside the same proven continuous segment*.

Do not infer continuity merely because two surviving observations are consecutive in the compressed `Vec`.

## Regression matrix

A focused regression should exercise the production composition, not only `stabilize_grids()` in isolation.

### 1. Missing-pair boundary

Create valid nonzero residual tracks for frames 0→1, then make frames 1→2 through 8→9 unavailable/rejected, then resume with valid tracks at 9→10.

Assert:

- no pre-gap sample participates in smoothing of the post-gap segment;
- every frame in the rejected interval has `table == None`;
- the post-gap path starts from zero/identity state rather than the pre-gap accumulator.

### 2. Scene cut whose optical fit fails

Use two unrelated synthetic frames so optical validation or `robust_similarity()` rejects the pair rather than producing a giant grid.

Assert the same segment reset as above. This is the gap missing from `scene_cut_resets_path`.

### 3. Non-consecutive decoded frames

Feed estimator results whose `frame_no` jumps. The current source explicitly `continue`s this case.

Assert the jump is a hard residual boundary and no mesh is held across it.

### 4. Ordinary continuous sparse sampling

Keep a control where valid residual samples are intentionally sparse but remain in one explicit continuous segment.

Assert table reuse still works there, so the repair does not throw away the intended interning/hold behavior.

## Secondary acceptance note

The code comment says the robust similarity step prevents moving foreground from dominating, but `robust_similarity()` only uses its inlier rejection to refit the global similarity. `residual_vectors()` is subsequently called with the full filtered `from`/`to` set, including points rejected from the similarity fit. Those residuals are then fed to the local grid. That may be intentional for parallax recovery, but a moving subject spanning several cells can therefore re-enter as local “camera” motion. This deserves a real-footage foreground stress test before sponsor acceptance; it is recorded here as a risk, not the primary blocker.

## Publication receipts

Direct publication was attempted against the exact #1228 head through both available upstream GitHub surfaces:

1. formal PR review → GitHub `403 Resource not accessible by integration`;
2. fallback top-level PR conversation comment → the same GitHub `403 Resource not accessible by integration`.

The connector is authenticated and owned-repository writes are live, so this is a target-repository installation/permission boundary, **not** evidence that GitHub writes are generally unavailable.

No external comment, sponsor contact, Algora attempt, claim, payout mutation, or duplicate feature PR was falsely claimed.

## Handoff

A publisher-capable seat should lift the blocking section + regression contract onto #1228 while preserving the carrier author's ownership. If the head moves, re-check the exact implementation first.

If #1228 consumes this repair, the next acceptance work should be real-footage validation across:

- no-gyro handheld motion;
- hard scene cuts / dropped frames;
- moving foreground crossing several mesh cells;
- low-texture tracking loss/recovery;
- preview/export and queued-render replay.

The bounty remains competitive and sponsor-selected.
