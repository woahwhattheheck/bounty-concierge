"""Authority-aware verification wrappers for public sponsor-adjudication APIs."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .artifacts import verify_artifacts as _verify_artifacts_integrity
from .artifacts import verify_report as _verify_report_integrity
from .authority import verify_report_authority
from .common import AdjudicationError, load_strict


def verify_report(report: Any) -> Dict[str, Any]:
    """Verify report integrity and retained sponsor-event host authority."""
    verified = _verify_report_integrity(report)
    verify_report_authority(verified["program"], verified["sponsor_events"])
    return verified


def verify_artifacts(out_dir: str | Path) -> Dict[str, Any]:
    """Verify artifact receipt/files plus sponsor-event host authority."""
    receipt = _verify_artifacts_integrity(out_dir)
    report = load_strict(Path(out_dir) / "adjudication.json")
    verified = verify_report(report)
    if verified["report_sha256"] != receipt["report_sha256"]:
        raise AdjudicationError("authority-aware report/receipt digest mismatch")
    return receipt


__all__ = ["verify_report", "verify_artifacts"]
