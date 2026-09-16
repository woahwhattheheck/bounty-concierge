# Submission transport renderer hardening

Exact-head review found a production-authority composition in the first transport successor:

```python
candidate = core._compile_candidate(request, authority_verifier=lambda _: True)
forged = core._production_receipt(candidate)
```

The first attempted repair installed an import-time shim. Review correctly showed that `importlib.reload()` or `runpy.run_module()` could execute the unchanged core source again and restore the predecessor renderer. The final closure changes the core source itself rather than relying on package import order.

## Renderer-free core invariant

`concierge._submission_transport_router_core` is a digest-gated loader for one renderer-free source payload. Seven deterministic source shards are concatenated, decoded with strict base64 validation, and accepted only when both of these exact bindings hold:

- decoded length: `28619` bytes;
- decoded SHA-256: `ffaa12e964799dc173e9cd2b7dad47c73b91fc35aab1a05703c70527f91aec6d`.

The payload contains no module-level `_production_receipt`, `_render_production_receipt`, or equivalent private callable that accepts internal candidate state and emits a production receipt. Production rendering exists only inside `compile_transport_decision(request)`, after that call constructs and applies its fixed root-owned host-ledger verifier.

The loader removes the two stopped predecessor names before decoding because `importlib.reload()` retains the existing module dictionary. A fresh `runpy.run_module()` execution receives the same digest-verified renderer-free payload. The core therefore does not depend on `concierge.__init__`, wrapper import order, or a one-time monkeypatch for this boundary.

## Test-only authority stays non-production

`_compile_transport_test_observation(request, authority_verifier=...)` remains an isolated algorithm-test seam. Even an always-true injected verifier can emit only `submission-transport-test-observation/v1`, with:

- `production_disposition=HOLD_TEST_ONLY_AUTHORITY`;
- `production_authority_valid=false`;
- `next_route=null`;
- no production `decision_sha256`;
- `external_send_authorized=false`.

The underlying candidate mapping has no module-level production renderer to compose with.

## Predecessor killers

`tests/test_submission_transport_renderer_boundary.py` attacks both source re-execution paths:

1. inject stale predecessor renderer attributes, call `importlib.reload(core)`, and prove they are removed;
2. execute the module in a fresh namespace with `runpy.run_module(...)` and prove no renderer appears;
3. create a `READY_FALLBACK` internal candidate with `lambda _: True` and prove it cannot be accepted as production input;
4. prove the only production compiler still returns `HOLD_PROVIDER_AUTHORITY_UNVERIFIED` without an exact fixed-ledger receipt.

This module remains send-free. It does not authorize provider actions, sponsor contact, submission, payment requests, acceptance claims, or cash claims.
