from __future__ import annotations

from copy import deepcopy
import json

import pytest
import requests

from concierge import bounty_contract as bc


NOW = "2026-09-13T09:30:00Z"
LATER = "2026-09-13T10:30:00Z"
REPO = "acme/widgets"
NUMBER = 17
ISSUE_URL = f"https://api.github.com/repos/{REPO}/issues/{NUMBER}"
COMMENTS_URL = f"{ISSUE_URL}/comments"


def label(label_id, name, *, description=None, color="0e8a16", default=False):
    return {
        "id": label_id,
        "node_id": f"LA_{label_id}",
        "name": name,
        "color": color,
        "default": default,
        "description": description,
    }


def assignee(user_id, login):
    return {
        "id": user_id,
        "node_id": f"U_{user_id}",
        "login": login,
        "type": "User",
    }


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = deepcopy(payload)
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return deepcopy(self.payload)


class Session:
    def __init__(self, issue_reads, comment_passes, *, status_code=200):
        self.issue_reads = [deepcopy(item) for item in issue_reads]
        self.comment_passes = [deepcopy(items) for items in comment_passes]
        self.issue_index = 0
        self.comment_pass = -1
        self.status_code = status_code
        self.calls = []

    def get(self, url, *, headers, params=None, timeout=15):
        self.calls.append((url, deepcopy(params)))
        if self.status_code >= 400:
            return Response({}, self.status_code)
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


def issue(**changes):
    value = {
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
        "labels": [
            label(1, "bounty", description="Paid work"),
            label(2, "90-rtc", description="Advertised reward: 90 RTC"),
        ],
        "assignees": [],
        "milestone": None,
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": NOW,
        "comments": 1,
    }
    value.update(changes)
    return value


def maintainer_comment(**changes):
    value = {
        "id": 501,
        "node_id": "IC_kwDOcontract501",
        "user": {"login": "maintainer", "type": "User"},
        "author_association": "OWNER",
        "body": "Acceptance requires the regression test to pass.",
        "created_at": "2026-09-02T00:00:00Z",
        "updated_at": "2026-09-02T00:00:00Z",
    }
    value.update(changes)
    return value


def external_comment(**changes):
    value = {
        "id": 700,
        "node_id": "IC_kwDOcontract700",
        "user": {"login": "other-builder", "type": "User"},
        "author_association": "NONE",
        "body": "I might try this.",
        "created_at": "2026-09-03T00:00:00Z",
        "updated_at": "2026-09-03T00:00:00Z",
    }
    value.update(changes)
    return value


def stable_session(issue_value=None, comments=None):
    issue_value = deepcopy(issue_value or issue())
    comments = deepcopy(comments if comments is not None else [maintainer_comment()])
    issue_value["comments"] = len(comments)
    return Session(
        [issue_value, issue_value, issue_value],
        [comments, comments],
    )


def baseline_receipt():
    return bc.capture_contract(
        REPO,
        NUMBER,
        session=stable_session(),
        captured_at=NOW,
    )


def verify(receipt, issue_value=None, comments=None, **kwargs):
    return bc.verify_contract(
        receipt,
        session=stable_session(issue_value, comments),
        checked_at=LATER,
        **kwargs,
    )


def test_capture_is_deterministic_and_privacy_safe():
    first = baseline_receipt()
    second = baseline_receipt()
    assert first == second
    serialized = json.dumps(first, sort_keys=True)
    assert "Repair the payment gate" not in serialized
    assert "Deliver the fix" not in serialized
    assert "Acceptance requires" not in serialized
    assert first["authority"] == {
        "external_mutation_performed": False,
        "acceptance_inferred": False,
        "payout_inferred": False,
        "cash_claim": False,
        "time_attested": False,
        "signature": "none",
        "sha256_role": "consistency_not_authentication",
    }
    assert bc.validate_receipt(first) == first


def test_unchanged_contract_passes():
    result = verify(baseline_receipt())
    assert result["disposition"] == "UNCHANGED"
    assert result["reason_codes"] == []
    assert result["live"]["contract_sha256"] == result["baseline_contract_sha256"]


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (lambda value: value.update(title=value["title"] + " revised"), "TITLE_CHANGED"),
        (lambda value: value.update(body=value["body"] + " Extra criterion."), "BODY_CHANGED"),
        (
            lambda value: value.update(
                labels=value["labels"] + [label(3, "priority", description="Urgent")]
            ),
            "LABELS_CHANGED",
        ),
        (lambda value: value.update(assignees=[assignee(41, "builder")]), "ASSIGNEES_CHANGED"),
        (
            lambda value: value.update(state="closed", state_reason="completed"),
            "ISSUE_STATE_CHANGED",
        ),
        (lambda value: value.update(locked=True), "LOCK_STATE_CHANGED"),
        (
            lambda value: value.update(
                milestone={
                    "number": 4,
                    "state": "open",
                    "due_on": "2026-10-01T00:00:00Z",
                    "title": "September release",
                    "description": "Ship paid work",
                }
            ),
            "MILESTONE_CHANGED",
        ),
        (
            lambda value: value.update(
                user={"login": "new-maintainer", "type": "User"}
            ),
            "ISSUE_AUTHORITY_CHANGED",
        ),
        (lambda value: value.update(id=1701), "SOURCE_IDENTITY_CHANGED"),
    ],
)
def test_issue_contract_drift_holds(mutator, reason):
    receipt = baseline_receipt()
    live = issue(updated_at=LATER)
    mutator(live)
    result = verify(receipt, live)
    assert result["disposition"] == "HOLD"
    assert reason in result["reason_codes"]


def test_maintainer_comment_edit_holds():
    receipt = baseline_receipt()
    changed = maintainer_comment(
        body="Acceptance now requires two extra benchmarks.",
        updated_at=LATER,
    )
    result = verify(receipt, issue(updated_at=LATER), [changed])
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["MAINTAINER_TERMS_CHANGED"]


def test_new_maintainer_term_holds():
    receipt = baseline_receipt()
    comments = [
        maintainer_comment(),
        maintainer_comment(
            id=502,
            node_id="IC_kwDOcontract502",
            body="Reward is now 45 RTC.",
            created_at=LATER,
            updated_at=LATER,
        ),
    ]
    result = verify(receipt, issue(updated_at=LATER), comments)
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["MAINTAINER_TERMS_CHANGED"]


def test_external_comment_churn_does_not_change_contract():
    receipt = baseline_receipt()
    comments = [maintainer_comment(), external_comment()]
    result = verify(receipt, issue(updated_at=LATER), comments)
    assert result["disposition"] == "UNCHANGED"
    assert result["reason_codes"] == []


def test_label_and_assignee_order_are_canonical():
    receipt = baseline_receipt()
    live = issue(
        labels=[
            label(2, "90-rtc", description="Advertised reward: 90 RTC"),
            label(1, "bounty", description="Paid work"),
        ],
        assignees=[assignee(42, "Beta"), assignee(41, "alpha")],
        updated_at=LATER,
    )
    baseline = issue(assignees=[assignee(41, "alpha"), assignee(42, "Beta")])
    receipt = bc.capture_contract(
        REPO,
        NUMBER,
        session=stable_session(baseline),
        captured_at=NOW,
    )
    result = verify(receipt, live)
    assert result["disposition"] == "UNCHANGED"


def test_label_description_drift_holds_without_storing_raw_description():
    receipt = baseline_receipt()
    serialized = json.dumps(receipt, sort_keys=True)
    assert "Advertised reward: 90 RTC" not in serialized
    live = issue(
        labels=[
            label(1, "bounty", description="Paid work"),
            label(2, "90-rtc", description="Advertised reward: 45 RTC"),
        ],
        updated_at=LATER,
    )
    result = verify(receipt, live)
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["LABELS_CHANGED"]


def test_duplicate_live_label_fails_closed():
    receipt = baseline_receipt()
    live = issue(
        labels=[
            label(1, "bounty", description="Paid work"),
            label(1, "bounty", description="Paid work"),
        ],
        updated_at=LATER,
    )
    result = verify(receipt, live)
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["LIVE_EVIDENCE_INVALID"]
    assert result["live"] is None


def test_pull_request_substitution_fails_closed():
    receipt = baseline_receipt()
    live = issue(pull_request={"url": "https://api.github.com/pulls/17"})
    result = verify(receipt, live)
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["SOURCE_IDENTITY_CHANGED"]


def test_source_disappearance_holds():
    receipt = baseline_receipt()
    result = bc.verify_contract(
        receipt,
        session=Session([], [], status_code=404),
        checked_at=LATER,
    )
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["SOURCE_UNAVAILABLE"]


def test_issue_generation_race_holds():
    receipt = baseline_receipt()
    before = issue()
    after = issue(body="changed during read", updated_at=LATER)
    session = Session(
        [before, after, after],
        [[maintainer_comment()], [maintainer_comment()]],
    )
    result = bc.verify_contract(receipt, session=session, checked_at=LATER)
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["LIVE_GENERATION_UNSTABLE"]


def test_comment_generation_race_holds():
    receipt = baseline_receipt()
    changed = maintainer_comment(updated_at=LATER)
    session = Session(
        [issue(), issue(), issue()],
        [[maintainer_comment()], [changed]],
    )
    result = bc.verify_contract(receipt, session=session, checked_at=LATER)
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["LIVE_GENERATION_UNSTABLE"]


def test_comment_pagination_limit_holds():
    receipt = baseline_receipt()
    comments = [
        external_comment(
            id=1000 + index,
            node_id=f"IC_{index}",
            created_at="2026-09-03T00:00:00Z",
            updated_at="2026-09-03T00:00:00Z",
        )
        for index in range(100)
    ]
    live = issue(comments=100, updated_at=LATER)
    session = Session([live], [comments])
    result = bc.verify_contract(
        receipt,
        session=session,
        max_pages=1,
        checked_at=LATER,
    )
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["LIVE_EVIDENCE_INCOMPLETE"]


def test_comment_count_mismatch_holds():
    receipt = baseline_receipt()
    live = issue(comments=2, updated_at=LATER)
    comments = [maintainer_comment()]
    result = bc.verify_contract(
        receipt,
        session=Session([live, live, live], [comments, comments]),
        checked_at=LATER,
    )
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["LIVE_EVIDENCE_INCOMPLETE"]


def test_receipt_contract_tampering_is_rejected():
    receipt = baseline_receipt()
    receipt["contract"]["labels"].append("forged")
    with pytest.raises(
        bc.BountyContractInputError,
        match="label is malformed|contract digest",
    ):
        bc.validate_receipt(receipt)


def test_receipt_envelope_tampering_is_rejected():
    receipt = baseline_receipt()
    receipt["captured_at"] = LATER
    with pytest.raises(bc.BountyContractInputError, match="receipt digest"):
        bc.validate_receipt(receipt)


def test_receipt_rejects_unknown_fields():
    receipt = baseline_receipt()
    receipt["trusted"] = True
    with pytest.raises(bc.BountyContractInputError, match="shape"):
        bc.validate_receipt(receipt)


def test_capture_rejects_boolean_issue_number():
    with pytest.raises(bc.BountyContractInputError, match="positive integer"):
        bc.capture_contract(REPO, True, session=stable_session(), captured_at=NOW)


def test_verify_rejects_boolean_max_pages():
    with pytest.raises(bc.BountyContractInputError, match="max_pages"):
        bc.verify_contract(
            baseline_receipt(),
            session=stable_session(),
            max_pages=True,
            checked_at=LATER,
        )


def test_summary_never_claims_cash_or_mutation():
    receipt = baseline_receipt()
    assert "mutation=false cash_claim=false" in bc.format_summary(receipt)
    verification = verify(receipt)
    assert "mutation=false cash_claim=false" in bc.format_summary(verification)
