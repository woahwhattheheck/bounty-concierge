# Proposed upstream PR

## Harden optimizer CFG walks against stack exhaustion

Fixes the stack-exhaustion class tracked by #27.

`optimize.c` has multiple recursive control-flow-graph walkers (`count_blocks`, `number_blks_r`, `find_levels_r`, and `convert_code_r`). Guarding only the historical `count_blocks()` site leaves the same pathological depth reachable in sibling passes.

This change adds an iterative CFG-depth preflight at the start of `opt_init()`, before any recursive optimizer traversal. It rejects graphs deeper than 128 blocks with an explicit compiler error rather than allowing process-stack exhaustion. The traversal records the greatest depth seen for each shared DAG block, so a shallow first path cannot mask a deeper later path; an unexpected cycle also fails closed at the bound.

Validation performed for the patch carrier:

- exact historical recursive shape crashes in a 64 KiB-stack child on a 50,000-block synthetic chain;
- preflight rejects the same chain before recursion;
- depth 128 remains accepted;
- shared-DAG deeper revisits are not hidden by marking;
- cycles fail closed;
- the carrier's hosted workflow clones pinned upstream `f15c258be8f269875b351f4c437992194c1baa2b`, applies the patch, builds upstream `filtertest`, validates an ordinary filter, and requires graceful rejection of a generated pathological conjunction.

No live `rpcapd` probing or service interaction was used.
