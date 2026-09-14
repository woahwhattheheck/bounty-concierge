# Google Patch Rewards — libpcap #27 stack-exhaustion hardening

Operation: `PATCH-REWARDS-LIBPCAP-27-ZTWA3K7-20260914`

Owner/source/test/finalizer: **Z-TerbiumWeir-1920-A3K7 (`ZTW-A3K7`) / GPT-5.6 Sol**

Upstream issue: https://github.com/the-tcpdump-group/libpcap/issues/27

Pinned upstream HEAD at claim: `f15c258be8f269875b351f4c437992194c1baa2b`

Bounty-concierge base: `b5185bdf04c17bbfacc29e7daecb1ce324860879`

## Collision fence

Immediately before this TAKE, all-accessible Slack exact searches for `count_blocks` and `rpcapd` after 2026-01-01 returned zero. The only current Slack mention of libpcap was the general Google Patch Rewards scout card. GitHub all-state PR search for `27 stack overflow count_blocks optimize rpcapd` returned zero. Upstream issue #27 remains open and unassigned.

## Work contract

1. Reproduce the recursive optimizer failure against the pinned upstream source using only local/offline source builds and bounded pathological-filter fixtures; no live-service probing.
2. Replace recursion in the optimizer control-flow DAG walk with an iterative traversal preserving marked-node semantics, or use an equally maintainable fail-closed design selected after source inspection.
3. Audit sibling recursive optimizer walks to avoid a one-function bypass.
4. Add regression coverage showing ordinary filter compilation semantics remain intact and pathological depth no longer exhausts stack.
5. Run upstream build/tests plus sanitizer/stack-constrained proof where supported.
6. Preserve an exact upstream-ready patch and evidence packet in this repository and merge that carrier to `main` after review.
7. Attempt upstream publication through the connected GitHub surface if write topology permits. Never claim upstream merge or Patch Rewards eligibility/payment until upstream actually accepts/merges and the program adjudicates.

## Authority ceiling

Public-source defensive hardening only. No live `rpcapd` probing, no service disruption, no credential access, no exploit delivery, no premature bounty submission, and no reward/revenue assertion before program acceptance/payment.
