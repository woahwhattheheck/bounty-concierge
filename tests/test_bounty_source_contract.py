# SPDX-License-Identifier: MIT
from __future__ import annotations

import base64

import requests

from concierge.bounty_source_contract import (
    SourceContractError,
    inspect_source_contract,
)


SHA = "a" * 40


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.closed = False

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"{self.status_code} response")
            error.response = self
            raise error

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected provider read")
        return self.responses.pop(0)

    def close(self):
        pass


def _file(path: str, text: str, blob: str = "blob-sha") -> dict:
    raw = text.encode("utf-8")
    return {
        "type": "file",
        "path": path,
        "sha": blob,
        "size": len(raw),
        "encoding": "base64",
        "content": base64.b64encode(raw).decode("ascii"),
    }


def test_ready_binds_ref_files_and_literal_without_echoing_literal():
    session = FakeSession(
        [
            FakeResponse(200, {"sha": SHA}),
            FakeResponse(200, _file("src/core.py", "def live_symbol():\n    return 1\n")),
        ]
    )

    result = inspect_source_contract(
        "owner/repo",
        ref="main",
        required_files=["src/core.py"],
        required_literals=[("src/core.py", "live_symbol")],
        token="test-token",
        session=session,
    )

    assert result["status"] == "READY"
    assert result["dispatch"] is True
    assert result["resolved_sha"] == SHA
    assert result["reason_codes"] == []
    assert result["required_files"][0]["blob_sha"] == "blob-sha"
    assert result["required_literals"][0]["present"] is True
    assert "live_symbol" not in str(result)
    assert len(session.calls) == 2
    assert session.calls[1][1]["params"] == {"ref": SHA}


def test_missing_required_file_is_stale_without_retry():
    session = FakeSession(
        [
            FakeResponse(200, {"sha": SHA}),
            FakeResponse(404, {"message": "Not Found"}),
        ]
    )

    result = inspect_source_contract(
        "owner/repo",
        ref=SHA,
        required_files=["tests/old_suite.py"],
        token="test-token",
        session=session,
    )

    assert result["status"] == "STALE"
    assert result["dispatch"] is False
    assert result["reason_codes"] == ["REQUIRED_FILE_MISSING"]
    assert result["required_files"][0]["exists"] is False
    assert len(session.calls) == 2


def test_missing_literal_is_stale_and_provider_error_fails_closed():
    stale_session = FakeSession(
        [
            FakeResponse(200, {"sha": SHA}),
            FakeResponse(200, _file("src/core.py", "def replacement():\n    pass\n")),
        ]
    )
    stale = inspect_source_contract(
        "owner/repo",
        ref="main",
        required_literals=[("src/core.py", "removed_symbol")],
        token="test-token",
        session=stale_session,
    )
    assert stale["status"] == "STALE"
    assert stale["reason_codes"] == ["REQUIRED_LITERAL_MISSING"]

    failing_session = FakeSession([FakeResponse(403, {"message": "rate limited"})])
    try:
        inspect_source_contract(
            "owner/repo",
            ref="main",
            required_files=["src/core.py"],
            token="test-token",
            session=failing_session,
        )
    except SourceContractError as exc:
        assert "GitHub read failed" in str(exc)
    else:
        raise AssertionError("provider ambiguity must fail closed")
    assert len(failing_session.calls) == 1
