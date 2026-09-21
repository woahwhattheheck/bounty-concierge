# tt-metal #55130 — program-cache mode-isolation acceptance

## Scope

This packet is a **non-competing acceptance artifact** for
`tenstorrent/tt-metal#55130` (advertised $5,000 bounty). The provider-assigned implementation
owner is `AJ0070`; assigned carrier `#56321` is not replaced or claimed here.

Source pins used for this packet:

- issue: `tenstorrent/tt-metal#55130`
- upstream main observed: `708e7f58aef9d089f0e5097d4a772c7c8ee129ea`
- assigned PR: `tenstorrent/tt-metal#56321`
- assigned PR head observed: `37a594463256d90aa05d2a8d9c955aedc53545b5`
- PR-head workflows observed at that SHA: PR Gate SUCCESS, Sanity tests SUCCESS,
  Verify changed tests SUCCESS, static checks SUCCESS.

No hardware execution or payout/acceptance claim is made.

## Why this is a separate acceptance seam

The assigned PR now carries `BiasGeluParams.fast_and_approximate` through both the classic
binary_ng tree and the Quasar mirror and includes the parameter in the program-cache attributes
for BIAS_GELU. That is the right *source shape*.

However, its changed bias-GELU tests do not currently contain a test that enables one device
program cache, calls **default**, **explicit false**, and **explicit true** modes on the same
shape, and checks the program-cache entry count. Numerical tests on cold compiles cannot prove
that exact and fast kernels receive distinct cache keys.

That omission matters because the mode changes compiled GELU code. If the cache key ever loses
`op_params`, a first call can populate the cache with one mode and a later call can reuse the
wrong compiled kernel while ordinary exact-only and fast-only tests still pass independently.

## Executable gate

`concierge/tt_metal_55130_cache_gate.py` checks both classic and Quasar trees for:

1. `BiasGeluParams.fast_and_approximate = false`.
2. `BinaryOpParams` carrying `BiasGeluParams`.
3. `"op_params"` present in the program-cache attribute names.
4. the cache value using `op_params` for BIAS_GELU and an empty value for other ops.
5. mode population in both tensor/tensor and tensor/scalar device-dispatch paths.
6. `OpConfig` consuming `operation_attributes.op_params`.
7. BIAS_GELU codegen emitting parametrized GELU (0.0 exact / 1.0 fast).
8. a Python regression that, in one cache lifetime, exercises default + explicit false +
   explicit true and inspects `num_program_cache_entries()`.

Run against a tt-metal checkout:

```bash
python concierge/tt_metal_55130_cache_gate.py --root /path/to/tt-metal --json
```

Exit 0 means PASS. Exit 2 means HOLD.

The scanner strips C/C++ comments before source checks, so comment-only decoys cannot satisfy the
contract.

## Upstream regression donor

The missing device test should use the existing tt-metal cache APIs and make the key semantics
observable directly:

```python
def test_bias_gelu_program_cache_mode_isolation(device):
    device.enable_program_cache()
    device.clear_program_cache()

    x_torch = torch.linspace(-5.0, 5.0, 1024, dtype=torch.float32).reshape(32, 32)
    b_torch = torch.full_like(x_torch, 0.5)
    x = ttnn.from_torch(x_torch, dtype=ttnn.float32, layout=ttnn.TILE_LAYOUT, device=device)
    b = ttnn.from_torch(b_torch, dtype=ttnn.float32, layout=ttnn.TILE_LAYOUT, device=device)

    # Default must be the same compiled mode as explicit False.
    ttnn.bias_gelu(x, b)
    after_default = device.num_program_cache_entries()
    ttnn.bias_gelu(x, b, fast_and_approximate_mode=False)
    assert device.num_program_cache_entries() == after_default

    # True changes compiled GELU code and therefore must add a program.
    ttnn.bias_gelu(x, b, fast_and_approximate_mode=True)
    after_true = device.num_program_cache_entries()
    assert after_true == after_default + 1

    # Switching back must reuse the exact program, not compile a third program.
    ttnn.bias_gelu(x, b, fast_and_approximate_mode=False)
    assert device.num_program_cache_entries() == after_true
```

A hardware lane may adapt placement/fixture details to the target test suite. The acceptance
property is the cache transition itself: default == explicit false, while explicit true is a
distinct compiled program.

## Hostile cases carried here

The internal test suite rejects:

- a missing cache regression despite otherwise-correct numerical tests;
- only one dispatch overload populating mode;
- `op_params` omitted from the BIAS_GELU cache value;
- codegen hardcoding one GELU mode;
- comment-only fake parameter declarations;
- a Quasar default drifting to fast;
- a cache test that exercises only default + true and never binds explicit false.

This gives the assigned carrier a deterministic acceptance target without opening a competing
upstream implementation PR.
