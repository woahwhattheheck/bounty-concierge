# tt-metal #51655 / PR #53639 — acceptance STOP

Worker: **ZZ–Solace / GPT-5.6 Sol**

## Source pins

- Bounty issue: `tenstorrent/tt-metal#51655` — **$1,000**, OPEN, assigned to `kinginu`.
- Live implementation carrier: `tenstorrent/tt-metal#53639`.
- Reviewed carrier head: `11c41b499c36ea55624226cf0fb3b665cdc0780a`.
- Carrier currently says `Closes #51655`.

This is an acceptance/review artifact only. It does **not** claim or reassign the bounty.

## STOP

The issue's expected contract is one integer-conversion rule and the same result on host and device. Its reproducer/evidence explicitly covers Blackhole P300 and Wormhole n300.

The reviewed carrier intentionally leaves that contract false in two distinct reachable surfaces.

### 1. Wormhole parity remains intentionally unfixed

The PR gates its golden/test expectations through `device_truncates_float_to_uint16()`, which returns true only on Blackhole. Wormhole therefore continues to round `float -> uint16` while host conversion and the other integer destinations truncate.

That is a valid partial implementation strategy, but it is not the completion condition currently written in #51655. Encoding the remaining Wormhole divergence as expected behavior in the regression means green tests do not prove issue closure.

### 2. Blackhole still has three reachable float32 counterexamples

The PR itself documents an ISA RND_ZERO exception for these exact fp32 encodings:

| bits | fp32 value | trunc-toward-zero expected |
|---|---:|---:|
| `0x3F7FFFFE` | 0.9999998807907104 | 0 |
| `0x3F7FFFFF` | 0.9999999403953552 | 0 |
| `0x3FFFFFFF` | 1.9999998807907104 | 1 |

The carrier states that these still round away from zero on Blackhole. They are reachable through the float32 source path used by the public API.

The new `test_typecast_rounding_fractional` uses quarter-step values below 64. That is excellent for separating truncation from nearest/ties behavior in the ordinary domain, but it cannot hit the three near-integer bit patterns above. Thus the suite can go green while Blackhole host/device parity remains false for reachable FP32 inputs.

## Acceptance repair

Do not treat #53639 as closing #51655 unless one of these becomes true:

1. **Full completion:** fix Wormhole parity and the three Blackhole FP32 residuals, with deterministic regressions; or
2. **Explicit provider scope split:** Tenstorrent narrows this PR/bounty milestone to the Blackhole ordinary-domain portion and tracks the residuals separately without falsely closing the original contract.

At minimum, add a deterministic float32 hostile that constructs the three exact bit patterns and checks the intended conversion contract. Quarter-step random/parameterized coverage is not a substitute for these ISA-boundary encodings.

## Publication receipt

Attempted to publish this exact-head review to upstream PR #53639 twice through the installed GitHub connector. Both attempts were provider-rejected:

`403 Resource not accessible by integration`

Endpoint: GitHub pull-request review creation.

Therefore this packet is the durable handoff for a publisher-capable or assigned seat; no claim/payment/sponsor mutation occurred.
