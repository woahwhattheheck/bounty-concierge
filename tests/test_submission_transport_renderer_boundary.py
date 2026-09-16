import importlib
from pathlib import Path
import runpy

import pytest

from concierge import _submission_transport_router_core as core
from test_submission_transport_router import request_with_attempt


def check(condition, message="check failed"):
    if not condition:
        raise AssertionError(message)


def assert_renderer_free(namespace):
    check("_production_receipt" not in namespace)
    check("_render_production_receipt" not in namespace)
    renderers = [
        name
        for name, value in namespace.items()
        if callable(value)
        and name.startswith("_")
        and "production" in name
        and "receipt" in name
    ]
    check(renderers == [])


def assert_injected_candidate_cannot_be_rendered(namespace):
    req = request_with_attempt()
    candidate = namespace["_compile_candidate"](
        req, authority_verifier=lambda _: True
    )
    check(candidate["candidate_disposition"] == "READY_FALLBACK")
    check(candidate["candidate_next_route"]["route_id"] == "sponsor-email")
    assert_renderer_free(namespace)
    with pytest.raises(namespace["SubmissionTransportInputError"]):
        namespace["compile_transport_decision"](candidate)
    fixed_ledger = Path("/var/lib/bounty-concierge/provider-attempt-authority")
    if not fixed_ledger.exists():
        production = namespace["compile_transport_decision"](req)
        check(production["disposition"] == "HOLD_PROVIDER_AUTHORITY_UNVERIFIED")
        check(production["next_route"] is None)


def test_importlib_reload_cannot_restore_predecessor_renderer():
    core._production_receipt = lambda candidate: candidate
    core._render_production_receipt = lambda candidate: candidate
    reloaded = importlib.reload(core)
    check(reloaded is core)
    assert_injected_candidate_cannot_be_rendered(vars(reloaded))


def test_runpy_fresh_namespace_cannot_restore_predecessor_renderer():
    namespace = runpy.run_module(
        "concierge._submission_transport_router_core", run_name="attacker"
    )
    assert_injected_candidate_cannot_be_rendered(namespace)
