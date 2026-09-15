# SPDX-License-Identifier: MIT
"""RustChain Bounty Concierge -- CLI tool for bounty hunters."""
__version__ = "0.1.0"

# Install the additive payoff continuity v3 dispatcher before callers import either
# payoff-path surface. Existing v2 semantics remain delegated to the landed core;
# normal v1 calls become fail-closed and historical replay moves behind an explicitly
# migration-only API. V3 keeps the landed CHAINED rendering vocabulary while its v3
# packet/receipt schemas and policy_heads carry the stronger policy/evidence semantics.
from . import payoff_path_policy_v3 as _payoff_path_policy_v3

_payoff_path_policy_v3.MODE = "CHAINED"
_payoff_path_policy_v3.install()
del _payoff_path_policy_v3

# Capture the verify-only owner-policy trust anchor during package bootstrap, before
# imported caller code gets a chance to replace per-process environment configuration.
# A missing/invalid launch-time public key or principal intentionally leaves v3
# owner-policy compilation fail-closed until the host restarts with valid authority.
from . import payoff_path_policy_authority as _payoff_path_policy_authority

del _payoff_path_policy_authority
