# SPDX-License-Identifier: MIT
"""RustChain Bounty Concierge -- CLI tool for bounty hunters."""
__version__ = "0.1.0"

# Reinvestment authority bootstrap MUST run before any other package submodule
# is returned to caller code. Python initializes a package before satisfying
# ``import concierge.<child>``; eager loading here therefore constructs the
# public isolated-worker closures before ordinary callers can obtain and mutate
# the importable API/transport helpers. The public module retires its one-shot
# factory immediately after construction.
from . import reinvestment_allocator as _reinvestment_allocator_bootstrap

del _reinvestment_allocator_bootstrap

# Install the additive payoff continuity v3 dispatcher before callers import either
# payoff-path surface. Existing v2 semantics remain delegated to the landed core;
# normal v1 calls become fail-closed and historical replay moves behind an explicitly
# migration-only API. V3 keeps the landed CHAINED rendering vocabulary while its v3
# packet/receipt schemas and policy_heads carry the stronger policy/evidence semantics.
from . import payoff_path_policy_v3 as _payoff_path_policy_v3

_payoff_path_policy_v3.MODE = "CHAINED"
_payoff_path_policy_v3.install()
del _payoff_path_policy_v3

# The claim/work primitive was recovered from its stale source branch without
# rewriting original product logic. Install the narrow security shim at package
# import so direct ``concierge.claim_work_authority`` consumers receive distinct
# retained-receipt authority and cannot transitively accept sponsor test-only
# unsigned adjudication.
from . import claim_work_authority_hardening as _claim_work_authority_hardening

_claim_work_authority_hardening.install()
del _claim_work_authority_hardening
