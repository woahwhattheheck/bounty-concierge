# Submission transport renderer hardening

`concierge.submission_transport_renderer_hardening` closes the direct-import composition found during exact-head review of the transport successor.

The stopped head exposed two ordinary core callables that could be chained:

```python
candidate = core._compile_candidate(request, authority_verifier=lambda _: True)
forged = core._production_receipt(candidate)
```

That composition could create a production-looking `submission-transport-decision/v2` receipt, `READY_FALLBACK` or `ALREADY_SUBMITTED`, a `decision_sha256`, and a next route without consulting the fixed host receipt ledger. Later verification would reject the receipt, but minting it contradicted the advertised production-authority boundary.

The package initializer now installs an additive hardening shim before any supported `concierge._submission_transport_router_core` import returns. The shim:

- replaces `compile_transport_decision(request)` with a compiler whose fixed-ledger verifier and renderer are local to the same call;
- replaces `verify_transport_decision(request, receipt)` with exact recompilation through that fixed path;
- deletes `_production_receipt` and any equivalent module renderer from the imported core;
- exposes no verifier, keyring, ledger path, environment selector, or candidate-to-production renderer;
- preserves the private injected test evaluator only for non-production route observations;
- keeps `external_send_authorized=false`, `global_outbound_lease_required=true`, and all sponsor/payment inference ceilings.

A fresh hostile test reproduces the injected candidate, proves it still predicts the candidate fallback for algorithm testing, proves no module production renderer remains, proves candidate state is rejected as production input, and proves the fixed-ledger production call holds when no exact retained provider receipt exists.

This is an import-time hardening layer, matching the repository's existing payoff-path and claim/work hardening pattern. It does not authorize provider actions, sponsor contact, submission, payment requests, or cash claims.
