from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
import requests

from concierge import bounty_contract as bc


REPO = "acme/widgets"
NUMBER = 17
NOW = "2026-09-13T09:30:00Z"
LATER = "2026-09-13T10:30:00Z"
ISSUE_URL = f"https://api.github.com/repos/{REPO}/issues/{NUMBER}"
COMMENTS_URL = f"{ISSUE_URL}/comments"


class Response:
    def __init__(self, payload):
        self.payload = deepcopy(payload)
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return deepcopy(self.payload)


class Session:
    def __init__(self, issue_reads, comment_passes):
        self.issue_reads = [deepcopy(item) for item in issue_reads]
        self.comment_passes = [deepcopy(items) for items in comment_passes]
        self.issue_index = 0
        self.comment_pass = -1

    def get(self, url, *, headers, params=None, timeout=15):
        if url == ISSUE_URL:
            payload = self.issue_reads[self.issue_index]
            self.issue_index += 1
            return Response(payload)
        if url == COMMENTS_URL:
            page = (params or {}).get("page", 1)
            if page == 1:
                self.comment_pass += 1
            rows = self.comment_passes[self.comment_pass]
            start = (page - 1) * 100
            return Response(rows[start : start + 100])
        raise AssertionError(f"unexpected URL {url}")


def issue():
    return {
        "url": ISSUE_URL,
        "repository_url": f"https://api.github.com/repos/{REPO}",
        "html_url": f"https://github.com/{REPO}/issues/{NUMBER}",
        "id": 1700,
        "node_id": "I_kwDOcontract17",
        "number": NUMBER,
        "title": "[BOUNTY] Repair the payment gate — 90 RTC",
        "body": "Deliver the fix. Reward: 90 RTC.",
        "user": {"login": "maintainer", "type": "User"},
        "author_association": "OWNER",
        "state": "open",
        "state_reason": None,
        "locked": False,
        "labels": [
            {
                "id": 1,
                "node_id": "LA_1",
                "name": "bounty",
                "color": "0e8a16",
                "default": False,
                "description": "Paid work",
            }
        ],
        "assignees": [],
        "milestone": None,
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": NOW,
        "comments": 1,
    }


def comment(body="Acceptance requires one regression test."):
    return {
        "id": 501,
        "node_id": "IC_kwDOcontract501",
        "user": {
            "login": "maintainer",
            "id": 41,
            "node_id": "U_41",
            "type": "User",
        },
        "author_association": "OWNER",
        "body": body,
        "created_at": "2026-09-02T00:00:00Z",
        "updated_at": "2026-09-02T00:00:00Z",
    }


def stable_session():
    value = issue()
    comments = [comment()]
    return Session([value, value, value], [comments, comments])


def receipt():
    return bc.capture_contract(
        REPO,
        NUMBER,
        session=stable_session(),
        captured_at=NOW,
    )


def test_same_timestamp_maintainer_body_race_holds():
    baseline = receipt()
    value = issue()
    before = [comment("Acceptance requires one regression test.")]
    after = [comment("Acceptance now requires two paid benchmarks.")]
    result = bc.verify_contract(
        baseline,
        session=Session([value, value, value], [before, after]),
        checked_at=LATER,
    )
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["LIVE_GENERATION_UNSTABLE"]
    assert result["live"] is None


def test_numeric_zero_authority_tamper_is_rejected():
    baseline = receipt()
    baseline["authority"]["cash_claim"] = 0
    with pytest.raises(bc.BountyContractInputError, match="authority boundary"):
        bc.validate_receipt(baseline)


def test_emit_refuses_existing_file_without_mutating_it(tmp_path):
    target = tmp_path / "receipt.json"
    target.write_bytes(b"original\n")
    with pytest.raises(FileExistsError):
        bc._emit({"ok": True}, str(target), pretty=False)
    assert target.read_bytes() == b"original\n"


def test_emit_refuses_symlink_without_mutating_target(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    target = tmp_path / "target.json"
    target.write_bytes(b"original\n")
    link = tmp_path / "receipt.json"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises((FileExistsError, OSError)):
        bc._emit({"ok": True}, str(link), pretty=False)
    assert target.read_bytes() == b"original\n"


def test_emit_creates_private_regular_json_file(tmp_path):
    target = tmp_path / "receipt.json"
    bc._emit({"ok": True}, str(target), pretty=False)
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert target.is_file() and not target.is_symlink()
    if os.name == "posix":
        assert target.stat().st_mode & 0o777 == 0o600
