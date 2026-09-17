# SPDX-License-Identifier: MIT
"""RustChain Bounty Concierge -- CLI tool for bounty hunters."""
__version__ = "0.1.0"

# Seal the reinvestment authority surface while the package itself is initializing.
# Python initializes a parent package before returning any requested child module,
# so a normal caller cannot first obtain ``_reinvestment_allocator_api`` or
# ``_reinvestment_allocator_transport`` and poison their factory globals before the
# public closures capture them. Explicitly pre-seeding either internal name in
# ``sys.modules`` is rejected rather than trusted.
import sys as _sys

_reinvestment_internal_names = (
    f"{__name__}._reinvestment_allocator_api",
    f"{__name__}._reinvestment_allocator_transport",
)
if any(name in _sys.modules for name in _reinvestment_internal_names):
    raise ImportError(
        "reinvestment authority internals must not be preloaded before package initialization"
    )
from . import reinvestment_allocator as _reinvestment_allocator

del _reinvestment_allocator
del _reinvestment_internal_names
del _sys

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
