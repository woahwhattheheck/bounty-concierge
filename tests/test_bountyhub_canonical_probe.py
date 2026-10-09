# SPDX-License-Identifier: MIT
"""Focused synthetic canonical-source rejection and rate-budget checks."""
import unittest
from concierge.bountyhub_canonical_probe import check_shortlist


class FakeResponse:
    def __init__(self, status, payload=None, remaining="10"):
        self.status_code = status
        self._payload = payload
        self.content = b"{}"
        self.headers = {"X-RateLimit-Remaining": remaining}
    def json(self):
        return self._payload
    def close(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class CanonicalProbeFixture(unittest.TestCase):
    def test_removed_closed_open_and_prune_only_new_build(self):
        selected = {"targets":[
            {"repo":"fluxerapp/fluxer-meta","number":5},
            {"repo":"azerothcore/azerothcore-wotlk","number":22571},
            {"repo":"example/project","number":14},
        ]}
        session = FakeSession([
            FakeResponse(404),
            FakeResponse(200, {"number":22571,"state":"closed",
                "html_url":"https://github.com/azerothcore/azerothcore-wotlk/issues/22571"}),
            FakeResponse(200, {"number":14,"state":"open",
                "html_url":"https://github.com/example/project/issues/14"}),
        ])
        report = check_shortlist(selected, session=session, max_requests=3)
        self.assertEqual([r["disposition"] for r in report["records"]],
                         ["HOLD","PRUNE_NEW_BUILD","PROCEED_TO_PREFLIGHT"])
        self.assertEqual((report["open_count"],report["closed_count"]),(1,1))
        self.assertFalse(report["build_authority"])
        self.assertEqual(len(session.calls),3)
        self.assertTrue(all(kwargs["allow_redirects"] is False for _,kwargs in session.calls))

    def test_rate_stop_retains_unchecked(self):
        selected = {"targets":[{"repo":"sample/one","number":2},{"repo":"sample/two","number":3}]}
        session = FakeSession([FakeResponse(403,remaining="0")])
        report = check_shortlist(selected, session=session)
        self.assertEqual(report["request_count"],1)
        self.assertTrue(report["rate_limited"])
        self.assertEqual(report["records"][1]["reason"],"NOT_CHECKED")


if __name__ == "__main__":
    unittest.main()
