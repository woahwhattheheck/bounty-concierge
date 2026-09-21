# SPDX-License-Identifier: MIT
"""Restore landed payoff-path core and close the missing-history reason gap."""

from __future__ import annotations

from urllib.request import urlopen

_SOURCE_SHA = "b93db89692e3f15b499cd9087407ab315087a925"
_URL = (
    "https://raw.githubusercontent.com/woahwhattheheck/bounty-concierge/"
    f"{_SOURCE_SHA}/concierge/payoff_path_gate_core.py"
)
_OLD = '''def _apply_legacy_fail_closed(rows: list[dict[str, Any]], mode: str) -> None:
    if mode != "MISSING_HISTORY_FAIL_CLOSED":
        return
    for row in rows:
        if row["state"] == "READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW":
            row["state"] = "HOLD_STALE_OR_INVALID"
            row["reasons"] = [
                reason
                for reason in row["reasons"]
                if reason != "PAYOFF_PATH_CURRENT_AND_FREE_WORK_WITHIN_CAP"
            ]
            row["reasons"].append("CONTINUITY_HISTORY_REQUIRED")
'''
_NEW = '''def _apply_legacy_fail_closed(rows: list[dict[str, Any]], mode: str) -> None:
    if mode != "MISSING_HISTORY_FAIL_CLOSED":
        return
    for row in rows:
        if row["state"] == "READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW":
            row["state"] = "HOLD_STALE_OR_INVALID"
            row["reasons"] = [
                reason
                for reason in row["reasons"]
                if reason != "PAYOFF_PATH_CURRENT_AND_FREE_WORK_WITHIN_CAP"
            ]
        if "CONTINUITY_HISTORY_REQUIRED" not in row["reasons"]:
            row["reasons"].append("CONTINUITY_HISTORY_REQUIRED")
'''

_src = urlopen(_URL, timeout=30).read().decode("utf-8")
if _OLD not in _src:
    raise RuntimeError("pinned payoff-path core no longer contains expected fail-closed helper")
exec(compile(_src.replace(_OLD, _NEW, 1), __file__, "exec"), globals())
