# Reinvestment authority pre-import and reload boundary

This note defines the exact same-process import guarantee for the v4 realized-reinvestment review surface.

## Guaranteed ordinary package-import sequence

Python initializes `concierge` before returning any requested `concierge.<child>` submodule to ordinary importing caller code. During that parent-package initialization, the retained bootstrap first evicts pre-seeded reinvestment public/API/transport child entries and then imports `concierge.reinvestment_allocator` before control can return to the caller requesting a reinvestment child. That public module consumes `_reinvestment_allocator_api.build_api` exactly once and constructs the exported compile/current-verification/integrity/scope closures.

After successful construction, the public module records `_REINVESTMENT_AUTHORITY_API_CONSUMED=True` on the already-initialized parent package and removes both `build_api` and the API module's `make_worker_invoker` alias. `_reinvestment_allocator_api.py` checks that parent-package consumption marker before importing the transport factory or defining `build_api`, so ordinary `importlib.reload(api)` raises `ImportError` before source re-execution can recreate the private factory. The public module checks the same marker before its canonical import path consumes the private factory, so ordinary `importlib.reload(reinvestment_allocator)` also fails closed rather than attempting a second authority build. The module-mode `__main__` path is handled first and delegates to the already initialized canonical module, preserving `python -m concierge.reinvestment_allocator` without weakening the one-shot boundary.

The previously demonstrated helper-first sequence cannot occur through ordinary package import: asking for `_reinvestment_allocator_api` or `_reinvestment_allocator_transport` first still completes the parent-package reinvestment bootstrap and seals the public authority closures before control returns to the caller. Later helper rebinding cannot replace the already captured `invoke_worker` closure.

Retained hostile predecessors cover API-first and transport-first package import followed by helper poisoning and a complete valid detached-RSA/provider execution. The #233 predecessor separately covers forged pre-seeded public/API/transport modules. Additional predecessors cover clean private-API reload, transport-poisoned private-API reload, public-module reload, and module-mode CLI delegation. The workflow runs the battery on Python 3.9 and 3.13 under both normal and `python -O` execution.

## Explicit non-claim

This is **not** a machine-strong sandbox against arbitrary code already controlling or mutating the interpreter. In particular, this carrier does not claim to defeat a caller that mutates the parent-package consumption marker after trusted bootstrap, rewrites `sys.modules` after trusted bootstrap, replaces import machinery, rewrites package bytes, reflectively rewrites live function/closure internals, replaces the interpreter, or defeats the operating-system process boundary. Those are hostile same-process mutation capabilities, not ordinary package import or ordinary reload. The retained #233 bootstrap independently rejects forged child-module pre-seeding that exists before the first trusted package import.

Consequential consumers must not infer authority merely from an in-process object. Current commercial evidence remains the v4 detached RSA authority verified in a fresh `python -I` worker, and `verify_reinvestment_receipt_current(...)` must reacquire provider state. The external private signing key remains outside the review process.

No bootstrap or reload state authorizes sponsor/customer contact, submission, spend, transfer, payment, wallet mutation, accounting or tax treatment, payout/cash assertion, future-return promise, or revenue recognition. The output remains advisory owner-review evidence only.
