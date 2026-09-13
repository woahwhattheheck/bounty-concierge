from __future__ import annotations

from copy import deepcopy

import pytest
import requests

from concierge import bounty_contract as bc


NOW = "2026-09-13T09:30:00Z"
LATER = "2026-09-13T10:30:00Z"
REPO = "acme/widgets"
NUMBER = 17
ISSUE_URL = f"https://api.github.com/repos/{REPO}/issues/{NUMBER}"
COMMENTS_URL = f"{ISSUE_URL}/comments"
COMMENT_TIME = "2026-09-02T00:00:00Z"


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
            if self.issue_index >= len(self.issue_reads):
                raise AssertionError("unexpected issue read")
            payload = self.issue_reads[self.issue_index]
            self.issue_index += 1
            return Response(payload)
        if url == COMMENTS_URL:
            page = (params or {}).get("page", 1)
            if page == 1:
                self.comment_pass += 1
            if self.comment_pass >= len(self.comment_passes):
                raise AssertionError("unexpected comment pass")
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
        "body": "Deliver the fix, tests, and docs. Reward: 90 RTC.",
        "user": {"login": "maintainer", "type": "User"},
        "author_association": "OWNER",
        "state": "open",
        "state_reason": None,
        "locked": False,
        "labels": [],
        "assignees": [],
        "milestone": None,
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": NOW,
        "comments": 1,
    }


def comment(*, body="Acceptance requires the regression test.", association="OWNER"):
    return {
        "id": 501,
        "node_id": "IC_kwDOcontract501",
        "user": {"login": "maintainer", "type": "User"},
        "author_association": association,
        "body": body,
        "created_at": COMMENT_TIME,
        "updated_at": COMMENT_TIME,
    }


def stable_session():
    item = issue()
    row = comment()
    return Session([item, item, item], [[row], [row]])


def baseline_receipt():
    return bc.capture_contract(
        REPO,
        NUMBER,
        session=stable_session(),
        captured_at=NOW,
    )


def test_same_timestamp_comment_body_edit_is_generation_unstable():
    receipt = baseline_receipt()
    item = issue()
    session = Session(
        [item, item, item],
        [
            [comment(body="Reward remains 90 RTC")],
            [comment(body="Reward is now 5 RTC")],
        ],
    )
    result = bc.verify_contract(receipt, session=session, checked_at=LATER)
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["LIVE_GENERATION_UNSTABLE"]


def test_same_timestamp_comment_authority_edit_is_generation_unstable():
    receipt = baseline_receipt()
    item = issue()
    session = Session(
        [item, item, item],
        [[comment(association="OWNER")], [comment(association="MEMBER")]],
    )
    result = bc.verify_contract(receipt, session=session, checked_at=LATER)
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["LIVE_GENERATION_UNSTABLE"]


def test_authority_boolean_cannot_be_replaced_by_numeric_zero():
    receipt = baseline_receipt()
    receipt["authority"]["cash_claim"] = 0
    with pytest.raises(bc.BountyContractInputError, match="authority boundary"):
        bc.validate_receipt(receipt)


def test_emit_refuses_to_clobber_existing_receipt(tmp_path):
    target = tmp_path / "baseline.json"
    target.write_text("sentinel\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        bc._emit({"ok": True}, str(target), pretty=False)
    assert target.read_text(encoding="utf-8") == "sentinel\n"


def test_emit_refuses_to_follow_symlink(tmp_path):
    target = tmp_path / "baseline.json"
    target.write_text("sentinel\n", encoding="utf-8")
    link = tmp_path / "receipt.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(FileExistsError):
        bc._emit({"ok": True}, str(link), pretty=False)
    assert target.read_text(encoding="utf-8") == "sentinel\n"
