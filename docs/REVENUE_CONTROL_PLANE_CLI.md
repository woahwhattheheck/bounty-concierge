# Revenue control-plane CLI

`concierge-revenue` is a thin launcher over the repository's existing, evidence-bound revenue authority modules. It does **not** duplicate their business logic and does not add buyer contact, provider mutation, payout, cash, award, or revenue-recognition authority.

## Why this exists

The repository's cash-cycle capabilities grew as deliberately isolated modules (`payoff_path_gate`, collection custody/request, receivables aging, payout dispute/escalation, settlement/closeout, and related controls). Executable authorities are directly runnable with `python -m`, but operators otherwise need to remember internal Python paths. The control-plane gives those executable authorities stable, product-level names while preserving each canonical module as the only source of truth.

## Discovery

```bash
concierge-revenue --list
concierge-revenue --list-json
concierge-revenue --help
```

The JSON form reports each stable target, its backing module, and its purpose. Discovery imports no target module and therefore cannot trigger target initialization or network activity.

## Invocation

Everything after the target is passed to the target's existing `main(argv)` entrypoint unchanged:

```bash
concierge-revenue cash-cycle compile closeout.json history.json bindings.json policy.json receipt.json
concierge-revenue cash-cycle verify receipt.json closeout.json history.json bindings.json policy.json
concierge-revenue payoff-path --help
concierge-revenue receivables-aging --help
concierge-revenue payout-dispute --help
concierge-revenue settlement --help
```

The target's own parser remains authoritative for its arguments, compile/verify modes, safety checks, files, and return codes. In particular, `--help` after a target belongs to that target.

## Stable targets

| Target | Canonical module |
| --- | --- |
| `cash-cycle` | `concierge.cash_cycle_review` |
| `closeout` | `concierge.revenue_closeout` |
| `collection-request` | `concierge.collection_request` |
| `contract-qualification` | `concierge.contract_qualification` |
| `distribution-fulfillment` | `concierge.distribution_fulfillment` |
| `payoff-path` | `concierge.payoff_path_gate` |
| `payout-dispute` | `concierge.payout_dispute` |
| `payout-escalation` | `concierge.payout_escalation` |
| `realized-economics` | `concierge.realized_unit_economics` |
| `receivables-aging` | `concierge.receivables_aging` |
| `settlement` | `concierge.revenue_settlement` |

`collection_custody` and `submission_custody` are intentionally **not** listed: they are library authorities and do not expose a `main(argv)` CLI contract. The control-plane never advertises a target it cannot execute.

## Boundary and failure behavior

The launcher constructs one fixed routing generation at module initialization. Its exported `COMMANDS` inspection view is immutable, and dispatch/discovery close over that original generation rather than consulting a caller-rebindable public mapping. Adding, replacing, deleting, or rebinding the exported registry therefore cannot add an executable target or redirect an existing target. User input is never interpreted as an arbitrary Python module path.

Targets are imported lazily only after a known target is selected. Unknown targets, missing modules, modules without callable `main(argv)`, and ordinary exceptions raised while importing or resolving the target entrypoint fail closed with exit code `2`. Import/entrypoint-resolution exception text is not echoed because nested exceptions can contain machine paths or credential-like environment data. `BaseException` control flow such as `SystemExit` remains unsuppressed.

A target may return an integer exit code or `None` (normalized to `0`). Other return shapes fail closed. After a target `main(argv)` is resolved, its execution occurs outside the import-sanitization boundary: the target's own `SystemExit` and other exceptions propagate unchanged so its canonical behavior is not reclassified by the launcher.

## Authority

`concierge-revenue` is routing, not authority. A target's receipt/verifier and source evidence remain canonical. A successful launcher invocation does **not** by itself prove a buyer reply, award, payment, cash receipt, settlement, or recognized revenue. It never contacts a buyer, submits a claim, mutates Stripe/bank/wallet state, accepts terms, or spends funds unless the selected canonical target itself explicitly and independently has such authority.
