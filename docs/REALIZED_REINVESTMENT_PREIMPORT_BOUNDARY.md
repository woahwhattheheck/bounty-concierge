# Reinvestment authority pre-import boundary

This note defines the exact same-process import guarantee for the v4 realized-reinvestment review surface.

## Guaranteed ordinary package-import sequence

Python initializes `concierge` before returning any `concierge.<child>` submodule to ordinary importing caller code. `concierge.__init__` therefore eagerly imports `concierge.reinvestment_allocator` as its first package action. That public module consumes `_reinvestment_allocator_api.build_api` exactly once, constructs the exported compile/current-verification/integrity/scope closures, and immediately removes both `build_api` and the API module's `make_worker_invoker` alias.

As a result, the previously demonstrated sequence

1. `import concierge._reinvestment_allocator_api`,
2. replace `api.make_worker_invoker`,
3. import `concierge.reinvestment_allocator`,
4. capture an attacker-selected worker invoker,

cannot occur through ordinary package import: step 1 itself completes package initialization and the public authority surface before control returns to the caller. The same ordering applies when `_reinvestment_allocator_transport` is the requested first child. Later rebinding of either helper module cannot replace the already captured `invoke_worker` closure.

The retained hostile predecessor imports each helper first in a fresh subprocess, confirms the public surface is already initialized and the one-shot API factory is retired, poisons both helper aliases, and then runs the existing valid externally signed case. It must still execute through the isolated worker. The workflow runs this predecessor on Python 3.9 and 3.13 under both normal and `python -O` execution.

## Explicit non-claim

This is **not** a machine-strong sandbox against arbitrary code already controlling the interpreter before package initialization. In particular, this carrier does not claim to defeat a caller that pre-seeds `sys.modules` with forged package children, replaces Python import machinery, rewrites package bytes, reflectively rewrites live function/closure internals, replaces the interpreter, or defeats the operating-system process boundary. Such a hostile same-process principal can fabricate local Python objects.

Consequential consumers therefore must not infer authority merely from an in-process object. Current commercial evidence remains the v4 detached RSA authority verified in a fresh `python -I` worker, and `verify_reinvestment_receipt_current(...)` must reacquire provider state. The external private signing key remains outside the review process.

No bootstrap state authorizes sponsor/customer contact, submission, spend, transfer, payment, wallet mutation, accounting or tax treatment, payout/cash assertion, future-return promise, or revenue recognition. The output remains advisory owner-review evidence only.
