import copy
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work" / "high-value" / "microg-2994-rcs"
SPEC = importlib.util.spec_from_file_location("verify_acceptance", WORK / "verify_acceptance.py")
verify = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(verify)


def snapshot():
    return json.loads((WORK / "acceptance.json").read_text())


def test_snapshot_holds_source_and_hardware():
    result = verify.assess(snapshot())
    assert result["status"] == "HOLD_SOURCE_INTEGRATION_AND_HARDWARE"
    assert len(result["source_blockers"]) == 1
    assert len(result["hardware_blockers"]) == 3
    assert result["new_implementation_pr_allowed"] is False
    assert result["payout_claimed"] is False


def test_integrated_source_still_holds_hardware():
    data = snapshot()
    data["required_source_followups"][0]["state"] = "merged"
    result = verify.assess(data)
    assert result["status"] == "HOLD_HARDWARE"
    assert result["source_blockers"] == []
    assert len(result["hardware_blockers"]) == 3


def test_all_physical_gates_only_reaches_maintainer_review():
    data = snapshot()
    data["required_source_followups"][0]["state"] = "merged"
    for gate in data["device_gates"]:
        gate["status"] = "pass"
    result = verify.assess(data)
    assert result["status"] == "READY_FOR_MAINTAINER_REVIEW"
    assert result["new_implementation_pr_allowed"] is False
    assert result["payout_claimed"] is False


def test_rejects_duplicate_pr_permission():
    data = snapshot()
    data["routing"]["new_implementation_pr"] = "allowed"
    try:
        verify.assess(data)
    except verify.EvidenceError as exc:
        assert "duplicate implementation" in str(exc)
    else:
        raise AssertionError("duplicate implementation permission was accepted")


def test_rejects_unreconciled_bounty_total():
    data = copy.deepcopy(snapshot())
    data["issue"]["bounty_usd"] = 15000
    try:
        verify.assess(data)
    except verify.EvidenceError as exc:
        assert "reconcile" in str(exc)
    else:
        raise AssertionError("unreconciled bounty total was accepted")
