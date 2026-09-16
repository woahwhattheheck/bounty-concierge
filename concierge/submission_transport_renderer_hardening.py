# SPDX-License-Identifier: MIT
"""Close direct-import renderer composition for submission transport.

This additive installer follows the repository's existing package-import
hardening pattern.  It replaces the core production compiler with a fixed-host
compiler whose renderer is local to that call, replaces verification with an
exact fixed-path recompile, and removes the predecessor's module-level
``_production_receipt(candidate)`` callable before package import returns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _submission_transport_router_core as _core

_MARKER = "_renderer_composition_hardened"


def install(*, force: bool = False) -> None:
    """Install the fixed-ledger production compiler exactly once.

    ``force`` exists only for hostile tests that restore a predecessor-shaped
    attribute and prove the installer deletes it again.  Both paths install the
    same fail-closed production functions; no verifier or trust-root selector is
    accepted from the caller.
    """
    if getattr(_core, _MARKER, False) and not force:
        return

    fixed_ledger = Path("/var/lib/bounty-concierge/provider-attempt-authority")

    def compile_transport_decision(request: Any) -> dict[str, Any]:
        """Compile only after exact receipt custody in the fixed host ledger."""

        def fixed_host_receipt_authority_verifier(receipt: dict[str, Any]) -> bool:
            retained = _core._read_host_receipt_from_directory(
                fixed_ledger,
                receipt.get("operation_sha256"),
            )
            return retained == receipt

        candidate = _core._compile_candidate(
            request,
            authority_verifier=fixed_host_receipt_authority_verifier,
        )

        # Deliberately local: an injected candidate returned by the private test
        # evaluator has no module-level production renderer to compose with.
        receipt = {
            "schema": "submission-transport-decision/v2",
            "canonical_source_url": candidate["canonical_source_url"],
            "packet_sha256": candidate["packet_sha256"],
            "head_sha": candidate["head_sha"],
            "artifact_evidence_sha256": candidate["artifact_evidence_sha256"],
            "policy_id": candidate["policy_id"],
            "policy_sha256": candidate["policy_sha256"],
            "policy_evidence_sha256": candidate["policy_evidence_sha256"],
            "attempt_count": candidate["attempt_count"],
            "completed_attempts": candidate["completed_attempts"],
            "disposition": candidate["candidate_disposition"],
            "reason_codes": candidate["candidate_reason_codes"],
            "next_route": candidate["candidate_next_route"],
            "authority": {
                "provider_outcome_authority_required": bool(candidate["attempt_count"]),
                "all_recorded_outcomes_authority_verified": candidate[
                    "all_recorded_outcomes_authority_verified"
                ],
                "provider_authority_trust_root": "fixed-root-owned-provider-receipt-ledger",
                "canonical_packet_verifier": "submission_custody._verify_submission_packet",
                "caller_verifier_injection_supported": False,
                "caller_keyring_selection_supported": False,
                "caller_trust_root_selection_supported": False,
                "direct_core_production_safe": True,
                "module_renderer_composition_supported": False,
                "external_send_authorized": False,
                "global_outbound_lease_required": True,
                "sponsor_route_authenticity_inferred": False,
                "sponsor_acceptance_inferred": False,
                "payment_inferred": False,
                "cash_claim": False,
            },
        }
        receipt["decision_sha256"] = _core._sha256(receipt)
        return receipt

    def verify_transport_decision(request: Any, receipt: Any) -> bool:
        if type(receipt) is not dict:
            return False
        try:
            expected = compile_transport_decision(request)
        except _core.SubmissionTransportInputError:
            return False
        return expected == receipt

    _core.compile_transport_decision = compile_transport_decision
    _core.verify_transport_decision = verify_transport_decision

    # The stopped head exposed this callable and allowed:
    # _compile_candidate(..., lambda _: True) -> _production_receipt(candidate).
    # Delete it before any supported package import returns.
    if hasattr(_core, "_production_receipt"):
        delattr(_core, "_production_receipt")
    if hasattr(_core, "_render_production_receipt"):
        delattr(_core, "_render_production_receipt")

    setattr(_core, _MARKER, True)


install()
