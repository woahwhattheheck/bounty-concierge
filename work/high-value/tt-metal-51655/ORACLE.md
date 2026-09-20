# tt-metal #51655 — executable acceptance oracle

Worker: **ZZ-Sol-19 / GPT-5.6 Sol**

Extends the existing `ACCEPTANCE_STOP.md` with an executable, stdlib-only acceptance artifact. This is review/support work only; upstream implementation and bounty ownership remain with `kinginu`.

## Exact source fence

- Upstream issue: `tenstorrent/tt-metal#51655` — advertised **$1,000**, OPEN, assigned to `kinginu`.
- Upstream current main reviewed: `708e7f58aef9d089f0e5097d4a772c7c8ee129ea`.
- Assigned carrier: `tenstorrent/tt-metal#53639` @ `11c41b499c36ea55624226cf0fb3b665cdc0780a`, still open at review.
- Blackhole typecast blob: `d78ce8c9fa922f7b9491070eb8af393917814458`.
- Wormhole typecast blob: `bb5a5848dad46bfc5b6b40c27f27cc4362741f26`.
- Host tensor conversion blob: `c3c678557016af5962ac64f6a3e651b3b8e712cf`.
- ttnn sweep golden blob: `2b226f32a07cb9d6e51e95dc5a918af5d8c63056`.
- copy-op test blob: `340a330f03043af18c7f951dd240c4a957b801af`.
- LLK golden blob: `a03adaa505bba87c134434dc1bb58040319bd389`.

Current upstream main still contains the filed divergence: Blackhole and Wormhole float-to-uint16 route through `SFP_STOCH_RND`, while host conversion uses `static_cast`; the current ttnn uint16 golden explicitly models device rounding. The assigned PR changes only Blackhole and explicitly leaves Wormhole plus three Blackhole FP32 encodings outside full parity.

## What the oracle proves

`oracle.py` exhaustively decodes all 65,536 bfloat16 bit patterns with Python stdlib IEEE-754 conversion. It reproduces the issue's exact positive uint16 domain count (**18,305**) and the exact **576** values where the filed uint16 device rounding model disagrees with truncation.

It also encodes the three exact FP32 residuals documented by PR #53639:

| fp32 bits | value | issue-contract uint16 | hit by carrier quarter-step test? |
|---|---:|---:|---|
| `0x3F7FFFFE` | 0.9999998807907104 | 0 | no |
| `0x3F7FFFFF` | 0.9999999403953552 | 0 | no |
| `0x3FFFFFFF` | 1.9999998807907104 | 1 | no |

The carrier regression uses exact quarter steps `base + {0.25, 0.5, 0.75}` for bases 0..62. The oracle converts that stimulus to exact FP32 encodings and proves all three residual encodings are disjoint from its 189-value set. Green quarter-step tests therefore cannot establish full Blackhole float32 parity.

## Commands

```bash
python work/high-value/tt-metal-51655/oracle.py
python -m pytest -q tests/test_tt_metal_51655_oracle.py
```

Local source build of this packet: **9 tests passed**. The repository Unit Tests workflow runs pytest on `tests/` under Python 3.9 and 3.13, so the new regression is CI-visible.

## Acceptance consequence

The executable evidence supports the existing STOP: PR #53639 is useful partial work, but its current tests do not prove the original issue's stated host/device parity contract. Closure needs either (a) Wormhole plus the three FP32 residuals fixed with deterministic tests, or (b) an explicit provider-approved scope split that stops treating the partial Blackhole result as full closure.

No device run, upstream code mutation, bounty reassignment, or payment claim is made here.