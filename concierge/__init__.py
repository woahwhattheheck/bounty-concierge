# SPDX-License-Identifier: MIT
"""RustChain Bounty Concierge -- CLI tool for bounty hunters."""
__version__ = "0.1.0"

# Install the additive payoff-continuity extension before any public payoff gate
# surface is imported. Legacy-only ledgers still delegate to the exact landed core.
from . import payoff_path_gate_core as _payoff_path_gate_core
from . import payoff_path_continuity_v2 as _payoff_path_continuity_v2

_payoff_path_continuity_v2.install(_payoff_path_gate_core)
