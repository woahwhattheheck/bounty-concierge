from __future__ import annotations

from copy import deepcopy
import unittest

from concierge.bounty_availability import (
    BountyAvailabilityError,
    inspect_bounty_availability,
)


ISSUE_URL = "https://api.github.com/repos/acme/widgets/issues/17"
COMMENTS_URL = ISSUE_URL + "/comments"


def issue(*, state="open", updated_at="2026-09-13T09:00:00Z", comments=1):
    return {
        "id": 1700,
        "number": 17,
        "state": state,
        "updated_at": updated_at,
        "comments": comments,
        "title": "[BOUNTY] Widget fix — 30 RTC",
        "body": "Reward: 30 RTC",
    }


def comment(
    body,
    *,
    ident=99,
    association="OWNER",
    login="maintainer",
    updated_at="2026-09-13T09:01:00Z",
):
    return {
        "id": ident,
        "created_at": "2026-09-13T09:01:00Z",
        "updated_at": updated_at,
        "author_association": association,
        "body": body,
        "user": {"login": login, "type": "User"},
    }


class Response:
    def __init__(self, payload):
        self.payload = deepcopy(payload)

    def raise_for_status(self):
        return None

    def json(self):
        return deepcopy(self.payload)


class Session:
    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.calls = []

    def get(self, url, *, headers, params=None, timeout=15):
        self.calls.append((url, deepcopy(params)))
        if not self.sequence:
            raise AssertionError("unexpected provider read")
        expected_url, payload = self.sequence.pop(0)
        if expected_url != url:
            raise AssertionError(f"expected {expected_url}, got {url}")
        return Response(payload)


def stable_session(comments, *, issue_payload=None):
    issue_payload = issue_payload or issue(comments=len(comments))
    return Session(
        [
            (ISSUE_URL, issue_payload),
            (COMMENTS_URL, comments),
            (COMMENTS_URL, comments),
            (ISSUE_URL, issue_payload),
        ]
    )


class AvailabilityTests(unittest.TestCase):
    def test_open_issue_with_maintainer_acceptance_is_hold(self):
        comments = [
            comment(
                "**@markabramov1993 — Stage 1 pack accepted, 30 RTC**: "
                "10 hooks, 3 videos, 5 original meme concepts."
            )
        ]
        result = inspect_bounty_availability(
            "acme/widgets", 17, session=stable_session(comments)
        )
        self.assertEqual(result["disposition"], "HOLD")
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["reason_code"], "MAINTAINER_TERMINAL_OUTCOME")
        self.assertEqual(
            result["signal_codes"], ["MAINTAINER_ACCEPTANCE_SIGNAL"]
        )
        rendered = repr(result)
        self.assertNotIn("markabramov1993", rendered)
        self.assertNotIn("10 hooks", rendered)
        self.assertNotIn("maintainer", rendered)

    def test_external_acceptance_text_cannot_hold(self):
        comments = [
            comment(
                "@alice pack accepted, 30 RTC",
                association="NONE",
                login="random-user",
            )
        ]
        result = inspect_bounty_availability(
            "acme/widgets", 17, session=stable_session(comments)
        )
        self.assertEqual(result["disposition"], "CLEAR")
        self.assertTrue(result["dispatch"])

    def test_negated_acceptance_does_not_hold(self):
        comments = [
            comment("@alice submission not accepted; needs revisions.")
        ]
        result = inspect_bounty_availability(
            "acme/widgets", 17, session=stable_session(comments)
        )
        self.assertTrue(result["dispatch"])

    def test_question_quote_and_code_are_not_terminal_assertions(self):
        comments = [
            comment(
                "Is @alice submission accepted?\n"
                "> @bob pack accepted, 30 RTC\n"
                "```text\n@carol pack accepted, 30 RTC\n```\n"
                "Submissions remain open."
            )
        ]
        result = inspect_bounty_availability(
            "acme/widgets", 17, session=stable_session(comments)
        )
        self.assertTrue(result["dispatch"])

    def test_explicit_award_is_hold(self):
        comments = [comment("Awarded to @alice for the submission.")]
        result = inspect_bounty_availability(
            "acme/widgets", 17, session=stable_session(comments)
        )
        self.assertEqual(result["signal_codes"], ["MAINTAINER_AWARD_SIGNAL"])
        self.assertFalse(result["dispatch"])

    def test_explicit_full_capacity_is_hold(self):
        comments = [comment("Submissions closed — all slots filled.")]
        result = inspect_bounty_availability(
            "acme/widgets", 17, session=stable_session(comments)
        )
        self.assertEqual(
            result["signal_codes"], ["MAINTAINER_CAP_CLOSED_SIGNAL"]
        )

    def test_explicit_cancellation_is_hold(self):
        comments = [comment("Bounty cancelled due to scope change.")]
        result = inspect_bounty_availability(
            "acme/widgets", 17, session=stable_session(comments)
        )
        self.assertEqual(
            result["signal_codes"], ["MAINTAINER_CANCELLED_SIGNAL"]
        )

    def test_closed_issue_is_hold_even_without_terminal_comment(self):
        payload = issue(state="closed", comments=0)
        result = inspect_bounty_availability(
            "acme/widgets", 17, session=stable_session([], issue_payload=payload)
        )
        self.assertEqual(result["reason_code"], "ISSUE_NOT_OPEN")

    def test_same_timestamp_body_drift_is_detected(self):
        first = [
            comment(
                "Thanks, submissions remain open.",
                updated_at="2026-09-13T09:01:00Z",
            )
        ]
        second = [
            comment(
                "@alice pack accepted, 30 RTC",
                updated_at="2026-09-13T09:01:00Z",
            )
        ]
        payload = issue(comments=1)
        session = Session(
            [
                (ISSUE_URL, payload),
                (COMMENTS_URL, first),
                (COMMENTS_URL, second),
                (ISSUE_URL, payload),
            ]
        )
        result = inspect_bounty_availability(
            "acme/widgets", 17, session=session
        )
        self.assertEqual(result["reason_code"], "COMMENT_GENERATION_CHANGED")
        self.assertFalse(result["dispatch"])

    def test_issue_generation_drift_is_detected(self):
        before = issue(updated_at="2026-09-13T09:00:00Z", comments=0)
        after = issue(updated_at="2026-09-13T09:02:00Z", comments=0)
        session = Session(
            [
                (ISSUE_URL, before),
                (COMMENTS_URL, []),
                (COMMENTS_URL, []),
                (ISSUE_URL, after),
            ]
        )
        result = inspect_bounty_availability(
            "acme/widgets", 17, session=session
        )
        self.assertEqual(result["reason_code"], "ISSUE_GENERATION_CHANGED")

    def test_full_page_at_max_pages_is_fail_closed(self):
        full = [
            comment("Thanks for participating.", ident=index + 1)
            for index in range(100)
        ]
        payload = issue(comments=100)
        session = Session(
            [
                (ISSUE_URL, payload),
                (COMMENTS_URL, full),
                (COMMENTS_URL, full),
                (ISSUE_URL, payload),
            ]
        )
        result = inspect_bounty_availability(
            "acme/widgets", 17, session=session, max_pages=1
        )
        self.assertEqual(result["reason_code"], "COMMENT_HISTORY_TRUNCATED")

    def test_malformed_comment_fails_closed_as_error(self):
        bad = [comment("hello")]
        del bad[0]["user"]
        with self.assertRaises(BountyAvailabilityError):
            inspect_bounty_availability(
                "acme/widgets", 17, session=stable_session(bad)
            )

    def test_invalid_repo_and_bounds_are_rejected(self):
        with self.assertRaises(ValueError):
            inspect_bounty_availability("not-a-repo", 17)
        with self.assertRaises(ValueError):
            inspect_bounty_availability("acme/widgets", True)
        with self.assertRaises(ValueError):
            inspect_bounty_availability("acme/widgets", 17, max_pages=101)


if __name__ == "__main__":
    unittest.main()
