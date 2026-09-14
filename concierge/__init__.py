# SPDX-License-Identifier: MIT
"""RustChain Bounty Concierge -- CLI tool for bounty hunters."""
__version__ = "0.1.0"

# Install the additive payoff continuity v3 dispatcher before callers import either
# payoff-path surface. Existing v2 semantics remain delegated to the landed core;
# normal v1 calls become fail-closed and historical replay moves behind an explicitly
# migration-only API.
from .payoff_path_policy_v3 import install as _install_payoff_path_policy_v3

_install_payoff_path_policy_v3()
del _install_payoff_path_policy_v3
