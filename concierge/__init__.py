# SPDX-License-Identifier: MIT
"""RustChain Bounty Concierge -- CLI tool for bounty hunters."""
__version__ = "0.1.0"

# BEGIN_REINVESTMENT_AUTHORITY_BOOTSTRAP_SEAL
# Seal the reinvestment authority surface during package bootstrap. Importing any
# concierge submodule executes this package initializer first, so callers cannot
# obtain the private API/transport modules and rebind their launch graph before
# the public surface captures it. Explicitly discard pre-seeded module entries
# as well; a same-interpreter caller must not be able to win bootstrap by
# populating sys.modules before importing concierge.
import sys as _reinvestment_sys

for _reinvestment_suffix in (
    "reinvestment_allocator",
    "_reinvestment_allocator_api",
    "_reinvestment_allocator_transport",
):
    _reinvestment_sys.modules.pop(
        f"{__name__}.{_reinvestment_suffix}", None
    )
del _reinvestment_suffix

from . import reinvestment_allocator as _reinvestment_allocator

del _reinvestment_allocator
del _reinvestment_sys
# END_REINVESTMENT_AUTHORITY_BOOTSTRAP_SEAL

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

# Capture the verify-only owner-policy trust anchor during package bootstrap, before
# caller-controlled policy documents are evaluated. A missing/invalid launch-time
# public key or principal intentionally leaves v3 owner-policy compilation fail-closed
# until the host restarts with valid authority.
from . import payoff_path_policy_authority as _payoff_path_policy_authority

del _payoff_path_policy_authority

# Bind the Payoff Policy public/direct entrypoints to bootstrap-captured verifier and
# semantic functions. This removes dependence on later public module rebinding and
# ordinary reload, but does not claim that Python closure-held function objects are
# immutable against arbitrary same-interpreter object-graph mutation. Hosts needing
# that stronger boundary must run the gate in a controlled fresh interpreter.
from . import payoff_path_policy_secure_runtime as _payoff_path_policy_secure_runtime

_payoff_path_policy_secure_runtime.install()
del _payoff_path_policy_secure_runtime
