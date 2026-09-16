import inspect
from pathlib import Path

import pytest

from concierge import _submission_transport_router_core as core
from concierge import submission_transport_renderer_hardening as hardening
from test_submission_transport_router import request_with_attempt


def check(condition, message="check failed"):
    if not condition:
        raise AssertionError(message)


def test_injected_candidate_has_no_module_production_renderer():
    req = request_with_attempt()
    candidate = core._compile_candidate(req, authority_verifier=lambda _: True)
    check(candidate["candidate_disposition"] == "READY_FALLBACK")
    check(candidate["candidate_next_route"]["route_id"] == "sponsor-email")

    # Exact predecessor killer: simulate the stopped attribute, then prove the
    # import-time installer removes it and reinstalls only the fixed compiler.
    core._production_receipt = lambda value: value
    hardening.install(force=True)
    check(not hasattr(core, "_production_receipt"))
    check(not hasattr(core, "_render_production_receipt"))
    renderers = [
        name
        for name, value in vars(core).items()
        if callable(value)
        and name.startswith("_")
        and "production" in name
        and "receipt" in name
    ]
    check(renderers == [])

    # Candidate state is not accepted by the only production compiler. That
    # compiler re-evaluates the original request through its local fixed-ledger
    # verifier before constructing a v2 receipt.
    with pytest.raises(core.SubmissionTransportInputError):
        core.compile_transport_decision(candidate)
    check(list(inspect.signature(core.compile_transport_decision).parameters) == ["request"])

    fixed_ledger = Path("/var/lib/bounty-concierge/provider-attempt-authority")
    if not fixed_ledger.exists():
        production = core.compile_transport_decision(req)
        check(production["disposition"] == "HOLD_PROVIDER_AUTHORITY_UNVERIFIED")
        check(production["next_route"] is None)
