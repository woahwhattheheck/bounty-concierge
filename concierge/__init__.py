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

# Every ordinary/public v3 compile+verify path requires current detached authority
# outside caller-controlled work bytes. A host-only TEST_ONLY switch exists solely
# to preserve deterministic historical state-machine fixtures; production entrypoints
# must leave it unset.
from .payoff_path_policy_authority import install as _install_payoff_policy_authority

_install_payoff_policy_authority()
del _install_payoff_policy_authority
