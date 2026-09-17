# Reinvestment authority pre-import and reload boundary

This note defines the exact same-process import guarantee for the v4 realized-reinvestment review surface.

## Guaranteed ordinary package-import sequence

Python initializes `concierge` before returning any `concierge.<child>` submodule to ordinary importing caller code. `concierge.__init__` therefore eagerly imports `concierge.reinvestment_allocator` as its first package action. That public module consumes `_reinvestment_allocator_api.build_api` exactly once and constructs the exported compile/current-verification/integrity/scope closures before caller code can obtain either private helper.

After successful construction, the public module records `_REINVESTMENT_AUTHORITY_API_CONSUMED=True` on the already-initialized parent package and removes both `build_api` and the API module's `make_worker_invoker` alias. `_reinvestment_allocator_api.py` checks that parent-package consumption marker before importing the transport factory or defining `build_api`. Ordinary `importlib.reload(api)` therefore raises `ImportError` before source re-execution can recreate the private factory. The same fail-closed behavior is retained after `transport.make_worker_invoker` is rebound.

The previously demonstrated helper-first sequence cannot occur through ordinary package import: asking for `_reinvestment_allocator_api` or `_reinvestment_allocator_transport` first still completes parent-package initialization and seals the public authority closures before control returns to the caller. Later helper rebinding cannot replace the already captured `invoke_worker` closure.

Retained hostile predecessors cover API-first and transport-first package import followed by helper poisoning and a complete valid detached-RSA/provider execution. Separate predecessors cover clean API reload and transport-poisoned API reload. The workflow runs the battery on Python 3.9 and 3.13 under both normal and `python -O` execution. A module-mode CLI predecessor also requires `python -m concierge.reinvestment_allocator --help` to remain usable after the one-shot bootstrap.

## Explicit non-claim

This is **not** a machine-strong sandbox against arbitrary code already controlling or mutating the interpreter. In particular, this carrier does not claim to defeat a caller that pre-seeds or rewrites `sys.modules`, changes the parent-package consumption marker, replaces import machinery, rewrites package bytes, reflectively rewrites live function/closure internals, replaces the interpreter, or defeats the operating-system process boundary. Those are hostile same-process mutation capabilities, not ordinary package import or ordinary reload. Separate controls may narrow those surfaces independently.

Consequential consumers must not infer authority merely from an in-process object. Current commercial evidence remains the v4 detached RSA authority verified in a fresh `python -I` worker, and `verify_reinvestment_receipt_current(...)` must reacquire provider state. The external private signing key remains outside the review process.

No bootstrap or reload state authorizes sponsor/customer contact, submission, spend, transfer, payment, wallet mutation, accounting or tax treatment, payout/cash assertion, future-return promise, or revenue recognition. The output remains advisory owner-review evidence only.
