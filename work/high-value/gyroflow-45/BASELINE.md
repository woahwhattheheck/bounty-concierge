# High-value bounty census — Gyroflow #45 optical-only stabilization

Operation: `HV-GYROFLOW-45-R-CINDER17-20260919`  
Worker: ZZ-Cinder-17 · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream: `gyroflow/gyroflow`  
Pinned upstream master: `d918ab3594e539f25a67a9f1d2b8e042798f61f7`

## Economics and live issue state

- GitHub issue: https://github.com/gyroflow/gyroflow/issues/45
- Title: `Optical only stabilization`
- Primary GitHub state: OPEN, no assignee; GitHub Development panel shows no linked branch/PR.
- Algora currently lists an open **$500** bounty for #45 with three historical claims.
- Fresh TokenJunkieLabs Slack census before TAKE returned no #45 work.
- Upstream connector is read-only for this account.

The issue was opened in February 2022 with one sentence: “We can try to use just the optical flow to do the stabilization, without using gyro data.” Current source is materially ahead of that description.

## Current-source finding: literal issue is substantially implemented

Current `src/core/synchronization/autosync.rs` blob:
`cf0fa7003ab91dc5ed1dc79169bb510aa4e7d3e5`.

Current `src/core/synchronization/mod.rs` blob:
`c8160825a425a53cbfa1c74c701d6af78fa97ed0`.

At current master the existing autosync path explicitly handles clips with no gyro motion:

1. `AutosyncProcess::from_manager` detects `mode == "synchronize" && !stab.gyro.read().has_motion()`.
2. In that case it changes analysis from user sync windows to the **entire video**.
3. Frames are processed through the existing optical-flow / pose-estimation stack.
4. `PoseEstimator::process_detected_frames` estimates per-frame camera rotation from feature correspondences, with selectable pose methods.
5. `recalculate_gyro_data` turns those estimated rotations into timestamped `TimeIMU` gyro samples and quaternions.
6. At the end of no-gyro synchronization, the process writes the estimated samples into the active gyro source via `file_metadata.set_raw_imu(...)`, then calls `gyro.apply_transforms()`.
7. The normal stabilization pipeline can therefore consume motion derived from optical flow even though the original file supplied no gyro motion.

The repository also contains multiple optical-flow implementations (AKAZE, OpenCV PyrLK/DIS where enabled), multiple pose estimators, undistortion for optical-flow points, and UI/config plumbing for optical-flow methods.

That makes a blind “implement optical-only stabilization” PR highly likely to duplicate current architecture.

## Disposition

**PARTIALLY_SUPERSEDED / RESIDUAL-ACCEPTANCE HOLD.**

The $500 listing is real, but the 2022 issue description no longer specifies what is still missing relative to current master. Before any claim or new source work, a maintainer should identify one concrete residual acceptance gap, such as:

- exposing the existing no-gyro fallback as an explicit user-facing stabilization mode;
- improving its quality / pose selection / smoothing;
- adding deterministic regression coverage for a no-gyro clip;
- documenting supported optical-only behavior and limitations;
- or confirming that existing behavior fully closes #45 and the bounty should be retired.

Do **not** fork a parallel optical-only motion pipeline without that clarification.

## Safe next step for a claimant

A qualified claimant should first reproduce current no-gyro behavior on a small retained fixture and compare the resulting estimated gyro / stabilized output against a motion-bearing reference. If the path already satisfies the intended feature, close/retire the issue instead of writing duplicate code. If a quality or UX gap remains, scope only that residual.

## Authority boundary

No upstream claim, source mutation, payment state, bounty award, or maintainer communication was performed by this census packet.
