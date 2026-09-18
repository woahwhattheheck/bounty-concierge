# SPDX-License-Identifier: MIT
"""Source-pinned claim admission with an isolated transitive replay generation.

The original v2 implementation remains byte-preserved in the private impl file.
Public helper names are compatibility/diagnostic mirrors, not production
admission dependencies. The installed entrypoint imports only the isolated
verifier below; valuation policy and receipt schemas are unchanged.
"""
from __future__ import annotations

from concierge import _claim_economic_admission_impl as _compatibility
from concierge._claim_economic_generation import load_claim_generation as _load

# Retain the existing diagnostic/test imports, including private helper names.
# They deliberately refer to a DIFFERENT generation from the production graph.
for _name, _value in vars(_compatibility).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

verify_claim_economic_receipt = _load(ClaimEconomicAdmissionError)
del _load, _compatibility, _name, _value
