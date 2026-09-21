# Tenstorrent tt-metal #52037 — route-completeness corrective

This layer complements the merged numerical oracle for the **$1,500-advertised** `logaddexp / logaddexp2` bounty. It does not replace that oracle and does not claim implementation ownership, bounty entitlement, hardware validation, merge acceptance, or payout.

## Why this exists

Issue #52037 success criterion 4 requires **all three composition sites** and both `LOGADDEXP` variants to be covered. A host arithmetic oracle can prove that the stable identity is numerically sound, but it cannot prove that every source route stopped using the overflowing composition.

The static audit therefore checks six route/op pairs:

| source site | LOGADDEXP | LOGADDEXP2 |
|---|---|---|
| `binary_ng/device/binary_ng_utils.cpp` | required | required |
| `binary/common/binary_op_utils.cpp` FPU switch | required | required |
| `binary/common/binary_op_utils.cpp` later SFPU/common switch | required | required |

It fails closed if a required case disappears or the expected case count changes.

## Exact assigned-carrier fence

Read against assigned implementation PR `tenstorrent/tt-metal#52856` at exact head:

`74999c9a6fdaf75bf6a23be32abd1dead7b35946`

Source blobs:

- `binary_ng_utils.cpp`: `effba6ba9034a1b53052d93fad7e6ae2198a497f`
- `binary_op_utils.cpp`: `9a9ca5eae81a730a2561dbac92489ceb0fd18c50`

Static result on that generation: **6/6 required route/op pairs still contain a legacy composition or fallback**.

The binary-ng cases have a stable SFPU branch, but their non-SFPU fallback still uses `EXP/EXP2 -> ADD -> LOG/LOG2`. Both common-file switches retain the legacy pipelines for both variants.

That means the assigned head can contain a valid new SFPU operation while still failing criterion 4's source-route completeness requirement.

## Scanner contract

`route_audit.py`:

1. strips C/C++ comments before token analysis, so historical comments do not create false failures;
2. requires exactly one `LOGADDEXP` and one `LOGADDEXP2` case in binary-ng;
3. requires exactly two cases per variant in the common file and binds first/second occurrences to the FPU and later SFPU/common sites;
4. marks a route RED if its case contains the complete legacy exponential + add + log token chain;
5. fails closed on missing or extra cases.

The focused tests include a mixed-route hostile: a stable same-dtype branch plus a legacy fallback must remain RED. This prevents a narrow dispatch fix from masquerading as whole-route coverage.

## Run

```bash
python work/high-value/tt-metal-52037/route_audit.py --source-root /path/to/tt-metal
python -m pytest -q tests/test_tt_metal_52037_route_audit.py
python -m py_compile work/high-value/tt-metal-52037/route_audit.py tests/test_tt_metal_52037_route_audit.py
```

The scanner returns zero only when all six expected route/op blocks are present and none contains the complete legacy composition.

## Boundary of evidence

This is deliberately **static completeness evidence**. Passing it does not establish numerical accuracy, device execution, dtype coverage, Wormhole/Blackhole behavior, or performance. Those remain separate acceptance obligations. Conversely, failing it is decisive for criterion 4 on the pinned source generation: at least one issue-named legacy route remains present.
