# tt-metal #56908 — independent tile-index acceptance cross-check

Worker: **ZZ-Sol-19 / GPT-5.6 Sol**

This is an independent acceptance artifact for the advertised **$3,000** Tenstorrent issue `#56908`. The issue is assigned to `Adraca`; this packet does **not** change implementation ownership, assignment, or payout entitlement, and it does not open another upstream patch.

## Exact source fence

Reviewed upstream main: `708e7f58aef9d089f0e5097d4a772c7c8ee129ea`.

Pinned blobs:

- pre factory: `f3792b73baafafc69f46181a97bbb6d936e87084`
- normal post factory: `d742534049b8daa8729479803dead802c28c5b0b`
- Welford post factory: `3c03d4f4404bed739864984eadb7a42e580cd38c`
- pre reader: `2c1dfefcb0ddae83fe7634f3c01db063ee0f94e0`
- shared post reader: `bb08640b6709887671f66897769179c19f667485`
- post writer: `4e7b6424165da5b2b7a20e2de31cbf56b0fc9b1d`

The pinned main has three separable 2D indexing defects: the per-core base omits `tiles_per_core_x`, readers walk local rows without a full-row stride, and the shared post writer drains a flat consecutive page run. The Welford factory additionally configures its shared reader with full-row `Wt` / buffer sizing rather than the local column width.

## Executable oracle

The stdlib-only `oracle.py` derives each core's exact row-major page ownership and compares four models:

1. pinned-main old base + flat walk,
2. corrected base + flat walk,
3. old base + corrected full-row stride,
4. corrected base + corrected full-row stride.

| case | original | base only | stride only | base + stride |
|---|---:|---:|---:|---:|
| 8×1 cores, 1×8 tiles/core | 0/64 wrong | 0/64 | 0/64 | 0/64 |
| 4×1 cores, 4×8 tiles/core | 96/128 wrong | 0/128 | 96/128 | 0/128 |
| 4×2 cores, 4×8 tiles/core | 240/256 wrong | 192/256 | 192/256 | 0/256 |
| 4×4 cores, 8×8 tiles/core | 992/1024 wrong | 896/1024 | 768/1024 | 0/1024 |

That separates an important nuance: a single core-column masks the **row-stride** error because local width equals global width, but it does **not** mask the independent old-base error for `x > 0` when `tiles_per_core_x > 1`.

The model also proves:

- pre-all-gather output base must scale by `tiles_per_core_x`;
- post-all-gather stats base must scale by `tiles_per_core_x`;
- correcting the base while leaving the post writer flat still corrupts multi-column multi-row layouts;
- for the representative 4×2 case, a Welford reader configured with full-row `Wt` requests 64 pages for a 32-page local slice and 40 requested pages are outside that core's expected ownership set.

## Exact-head PR cross-check

This table records source-diff/test-surface coverage only. It is not a merge recommendation and does not replace provider review or required hardware evidence.

| required/mechanism surface | #56983 @ `e2e2e3a` | #57039 @ `f20fe66` | #57084 @ `7588575` |
|---|---|---|---|
| input base scales by rows/core | yes | yes | no |
| pre output base scales by rows/core | yes | yes | no |
| post stats base scales by rows/core | yes | yes | no |
| pre reader full-row stride/rebase | yes | yes | yes |
| post reader full-row stride/rebase | yes | yes | yes |
| post writer full-row stride | no | yes | no |
| Welford reader uses local `Wt` | no | yes | yes |
| Welford CB length local-width aware | no | yes | no |
| Welford block size local-width aware | no | yes | no |
| RMSNorm multi-row regression in diff | yes | yes | no |
| LayerNorm/Welford multi-row regression in diff | no | yes | no |
| required Wormhole receipt in PR body | no | no (explicitly pending) | no (explicitly not done) |

The actionable acceptance gaps are therefore concrete rather than stylistic: `#56983` leaves the shared output writer flat and leaves Welford's reader width/buffer configuration on the full row; `#57084` rebases reader rows but leaves the old per-core base/stat/output offsets and flat writer. `#57039` contains the modeled source/test surfaces, but its own PR body explicitly says required Wormhole hardware validation is pending, so host/source analysis cannot promote it to hardware-validated completion.

## Commands

```bash
python work/high-value/tt-metal-56908/oracle.py
python -m pytest -q tests/test_tt_metal_56908_oracle.py
```

Local packet result: **13 tests passed**.

No Wormhole execution, device correctness, PCC, performance, provider acceptance, payout, or assignment claim is made here.
