# Reinvestment authority pre-import and reload boundary

This note defines the exact same-process import guarantee for the v4 realized-reinvestment review surface.

## Guaranteed ordinary package-import sequence

Python initializes `concierge` before returning any requested `concierge.<child>` submodule to ordinary importing caller code. During that parent-package initialization, the retained #233 bootstrap first evicts pre-seeded reinvestment public/API/transport child entries and then imports `concierge.reinvestment_allocator` before control can return to the caller requesting a reinvestment child. That public module consumes `_reinvestment_allocator_api.build_api` exactly once and constructs the exported compile/current-verification/integrity/scope closures.

After successful construction, the public module removes both `build_api` and the API module's `make_worker_invoker` alias, then installs an exact-module meta-path reload guard. Ordinary `importlib.reload()` of either the private API module or the public reinvestment module therefore receives a no-op loader bound to the exact already-bootstrapped module object. Source is not re-executed, the retired factory is not recreated, and the already captured `invoke_worker` closure is unchanged even if caller-visible helper bindings are later rebound. Repeated ordinary reload remains a no-op.

The module-mode `__main__` path delegates to the canonical package-bootstrapped public module, preserving `python -m concierge.reinvestment_allocator` without consuming the private factory a second time.

The previously demonstrated helper-first sequence cannot occur through ordinary package import: asking for `_reinvestment_allocator_api` or `_reinvestment_allocator_transport` first still completes the parent-package reinvestment bootstrap and seals the public authority closures before control returns to the caller. Later helper rebinding cannot replace the already captured `invoke_worker` closure.

Retained hostile predecessors cover API-first and transport-first package import followed by helper poisoning and a complete valid detached-RSA/provider execution. The #233 predecessor separately covers forged pre-seeded public/API/transport modules. Additional predecessors cover repeated clean private-API reload, repeated public-module reload, helper-poisoned reload, and module-mode CLI delegation. The workflow runs the battery on Python 3.9 and 3.13 under both normal and `python -O` execution.

## Explicit non-claim

This is **not** a machine-strong sandbox against arbitrary code already controlling or mutating the interpreter. In particular, this carrier does not claim to defeat `sys.meta_path` surgery, direct loader execution, post-bootstrap `sys.modules` replacement, installed-source replacement, reflective live function/closure mutation, interpreter replacement, or defeat of the operating-system process boundary. Those are hostile same-process/import-machinery or host-takeover capabilities rather than ordinary package import, ordinary module rebinding, or ordinary `importlib.reload`. The retained #233 bootstrap independently rejects forged child-module pre-seeding that exists before the first trusted package import.

Consequential consumers must not infer authority merely from an in-process object. Current commercial evidence remains the v4 detached RSA authority verified in a fresh `python -I` worker, and `verify_reinvestment_receipt_current(...)` must reacquire provider state. The external private signing key remains outside the review process.

No bootstrap or reload state authorizes sponsor/customer contact, submission, spend, transfer, payment, wallet mutation, accounting or tax treatment, payout/cash assertion, future-return promise, or revenue recognition. The output remains advisory owner-review evidence only.
