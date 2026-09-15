# SPDX-License-Identifier: MIT
"""Public adapter for the evidence-bound submission transport router.

The routing engine stays isolated in ``_submission_transport_router_core``.
This adapter deliberately replaces its packet ingress with the repository's
canonical submission-custody verifier so packet semantics cannot drift between
the readiness/custody and transport layers.
"""

from __future__ import annotations

from typing import Any

from concierge import _submission_transport_router_core as _core
from concierge.submission_custody import SubmissionCustodyError, _verify_submission_packet


SubmissionTransportInputError = _core.SubmissionTransportInputError
AuthorityVerifier = _core.AuthorityVerifier

_PACKET_ERROR_MESSAGES = {
    "PACKET_DIGEST_MISMATCH": "packet_sha256 does not match canonical packet bytes",
    "PACKET_NOT_READY": "packet is not READY_FOR_HUMAN_SUBMISSION",
    "SOURCE_URL_INVALID": "canonical_source_url is invalid",
    "PACKET_AUTHORITY_MISMATCH": "packet authority is invalid",
}


def _canonical_packet_adapter(packet: Any) -> dict[str, Any]:
    """Validate through submission custody and normalize the fields core needs."""
    try:
        verified = _verify_submission_packet(packet)
    except SubmissionCustodyError as exc:
        detail = _PACKET_ERROR_MESSAGES.get(exc.code, "submission packet rejected")
        raise SubmissionTransportInputError(f"{detail} [{exc.code}]") from exc
    return {
        "canonical_source_url": verified["source_url"],
        "packet_sha256": verified["packet_sha256"],
        "head_sha": verified["head_sha"],
        "artifact_evidence_sha256": verified["artifact_digest"],
    }


# All engine entry points resolve _check_packet from the core module at call time.
# Install the canonical contract once during public-module import.  The private
# core is not the supported import surface.
_core._check_packet = _canonical_packet_adapter

compile_transport_operation = _core.compile_transport_operation
make_hmac_authority_verifier = _core.make_hmac_authority_verifier
compile_transport_decision = _core.compile_transport_decision
verify_transport_decision = _core.verify_transport_decision
strict_json_loads = _core.strict_json_loads
format_summary = _core.format_summary
main = _core.main

__all__ = [
    "AuthorityVerifier",
    "SubmissionTransportInputError",
    "compile_transport_operation",
    "make_hmac_authority_verifier",
    "compile_transport_decision",
    "verify_transport_decision",
    "strict_json_loads",
    "format_summary",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
