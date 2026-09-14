"""Authority-aware verification wrappers for public sponsor-adjudication APIs."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

from .artifacts import verify_artifacts as _verify_artifacts_integrity
from .artifacts import verify_report as _verify_report_integrity
from .authority import verify_report_authority
from .common import AdjudicationError, load_strict


def _authority_projection(verified: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in verified.items()
        if key not in {"report_sha256", "sponsor_authority_binding"}
    }


def _verify_zero_event_semantics(verified: Dict[str, Any]) -> None:
    if verified["sponsor_events"]:
        return
    if verified["claim_units"]:
        raise AdjudicationError("report without sponsor events may not contain sponsor claim units")
    summary = verified["summary"]
    if (
        summary.get("sponsor_event_count") != 0
        or summary.get("claim_unit_count") != 0
        or summary.get("sponsor_verified_finding_count") != 0
    ):
        raise AdjudicationError("report without sponsor events has sponsor-derived summary posture")
    for finding in verified["findings"]:
        if (
            finding.get("status") != "submitted"
            or finding.get("action") != "OWNER_REVIEW"
            or finding.get("canonical_claim_unit_id") is not None
        ):
            raise AdjudicationError("report without sponsor events has sponsor-derived finding posture")


def verify_report(report: Any) -> Dict[str, Any]:
    """Verify report integrity plus exact historical host-authority generation."""
    verified = _verify_report_integrity(report)
    verify_report_authority(
        verified["program"],
        verified["sponsor_events"],
        verified["sponsor_authority_binding"],
        _authority_projection(verified),
    )
    _verify_zero_event_semantics(verified)
    return verified


def verify_artifacts(out_dir: str | Path) -> Dict[str, Any]:
    """Verify artifact receipt/files plus sponsor-event host authority."""
    receipt = _verify_artifacts_integrity(out_dir)
    report = load_strict(Path(out_dir) / "adjudication.json")
    verified = verify_report(report)
    if verified["report_sha256"] != receipt["report_sha256"]:
        raise AdjudicationError("authority-aware report/receipt digest mismatch")
    binding = verified["sponsor_authority_binding"]
    expected_manifest_sha = binding["manifest_sha256"] if isinstance(binding, dict) else None
    if receipt.get("authority_manifest_sha256") != expected_manifest_sha:
        raise AdjudicationError("authority-aware receipt/manifest binding mismatch")
    return receipt


__all__ = ["verify_report", "verify_artifacts"]
