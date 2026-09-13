import json
import pathlib
import sys

import pytest
import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge.bounty_preflight import (
    CLOSED,
    DELETED_OR_MOVED,
    OPEN,
    UNVERIFIABLE,
    LeadProvenance,
    format_json,
    format_markdown,
    preflight_bounty_listing,
)


class FakeResponse:
    def __init__(self, *, status=200, url, payload=None, history=()):
        self.status_code = status
        self.url = url
        self.payload = payload
        self.history = list(history)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, page, api=None, api_error=None):
        self.page = page
        self.api = api
        self.api_error = api_error
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None, allow_redirects=None):
        self.calls.append((url, headers or {}, allow_redirects))
        if url.startswith("https://api.github.com/"):
            if self.api_error:
                raise self.api_error
            return self.api
        return self.page


P = LeadProvenance(
    source_url="https://app.opire.dev/rewards/abc",
    source_timestamp="2026-09-13T06:30:00Z",
    bounty_amount="$1500",
    solver_count=0,
)


def issue(url, state):
    return FakeResponse(
        status=200,
        url=url,
        payload={"state": state, "html_url": url},
    )


def test_aggregator_open_but_canonical_github_closed():
    url = "https://github.com/acme/widget/issues/7"
    result = preflight_bounty_listing(
        url,
        P,
        session=FakeSession(FakeResponse(url=url), issue(url, "closed")),
    )
    assert result.authority_state == CLOSED


def test_redirect_to_deleted_target_is_not_open():
    old = "https://github.com/old/widget/issues/7"
    new = "https://github.com/new/widget/issues/7"
    result = preflight_bounty_listing(
        old,
        P,
        session=FakeSession(FakeResponse(status=404, url=new)),
    )
    assert result.authority_state == DELETED_OR_MOVED
    assert result.moved is True


def test_valid_open_issue_is_open():
    url = "https://github.com/acme/widget/issues/9"
    result = preflight_bounty_listing(
        url,
        P,
        session=FakeSession(FakeResponse(url=url), issue(url, "open")),
    )
    assert result.authority_state == OPEN
    assert result.canonical_repo == "acme/widget"
    assert result.issue_number == 9


def test_moved_but_live_target_uses_canonical_target_state():
    old = "https://github.com/old/widget/issues/9"
    new = "https://github.com/new/widget/issues/11"
    result = preflight_bounty_listing(
        old,
        P,
        session=FakeSession(
            FakeResponse(url=new, history=(object(),)),
            issue(new, "open"),
        ),
    )
    assert result.authority_state == OPEN
    assert result.moved is True
    assert result.canonical_repo == "new/widget"
    assert result.issue_number == 11


def test_api_unavailable_fails_closed():
    url = "https://github.com/acme/widget/issues/9"
    result = preflight_bounty_listing(
        url,
        P,
        session=FakeSession(
            FakeResponse(url=url),
            api_error=requests.Timeout("token=do-not-leak"),
        ),
    )
    assert result.authority_state == UNVERIFIABLE
    assert "token" not in result.reason


def test_cross_host_redirect_fails_closed_and_sends_no_token_to_page_resolution():
    old = "https://github.com/acme/widget/issues/9"
    session = FakeSession(FakeResponse(url="https://example.com/moved"))
    result = preflight_bounty_listing(old, P, token="secret", session=session)
    assert result.authority_state == UNVERIFIABLE
    assert "Authorization" not in session.calls[0][1]
    assert len(session.calls) == 1


def test_pull_request_payload_is_not_accepted_as_issue():
    url = "https://github.com/acme/widget/issues/9"
    api = FakeResponse(
        url=url,
        payload={"state": "open", "html_url": url, "pull_request": {}},
    )
    result = preflight_bounty_listing(
        url,
        P,
        session=FakeSession(FakeResponse(url=url), api),
    )
    assert result.authority_state == UNVERIFIABLE


def test_invalid_solver_count_rejected():
    url = "https://github.com/acme/widget/issues/9"
    with pytest.raises(ValueError, match="solver_count"):
        preflight_bounty_listing(
            url,
            LeadProvenance("src", "ts", solver_count=True),
            session=FakeSession(FakeResponse(url=url)),
        )


def test_json_and_markdown_are_deterministic_and_keep_provenance_separate():
    url = "https://github.com/acme/widget/issues/9"
    result = preflight_bounty_listing(
        url,
        P,
        session=FakeSession(FakeResponse(url=url), issue(url, "open")),
    )
    assert format_json(result) == format_json(result)
    payload = json.loads(format_json(result))
    assert payload["authority_state"] == OPEN
    assert payload["provenance"]["bounty_amount"] == "$1500"
    assert payload["provenance"]["solver_count"] == 0
    assert format_markdown(result) == format_markdown(result)


def test_markdown_flattens_and_escapes_provenance_text():
    url = "https://github.com/acme/widget/issues/9"
    provenance = LeadProvenance(
        "https://feed.example/a|b\nINJECT",
        "2026-09-13\r\nrow",
        "$10|20",
        0,
    )
    result = preflight_bounty_listing(
        url,
        provenance,
        session=FakeSession(FakeResponse(url=url), issue(url, "open")),
    )
    markdown = format_markdown(result)
    assert "a\\|b INJECT" in markdown
    assert "2026-09-13 row" in markdown
    assert "$10\\|20" in markdown
