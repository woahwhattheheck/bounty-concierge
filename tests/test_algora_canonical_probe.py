# SPDX-License-Identifier: MIT
"""Focused canonical Algora source checks using injected GitHub responses."""
import unittest
from concierge.algora_canonical_probe import inspect_algora_listing


def listing(number=6674):
    return {
        "listing_url": "https://algora.io/projectdiscovery/bounties",
        "canonical_issue_url": f"https://github.com/projectdiscovery/nuclei/issues/{number}",
        "listing_state": "OPEN", "advertised_usd": "100",
    }


def provider(state="open", *, archived=False, fork=False, prs=None):
    prs = [] if prs is None else prs
    def read(path):
        if path == "/repos/projectdiscovery/nuclei":
            return {"full_name": "projectdiscovery/nuclei", "archived": archived, "fork": fork}
        if path.startswith("/repos/projectdiscovery/nuclei/issues/"):
            n = int(path.rsplit("/", 1)[1])
            return {"number": n, "state": state, "html_url":
                    f"https://github.com/projectdiscovery/nuclei/issues/{n}",
                    "assignees": [], "state_reason": None}
        if "/pulls?" in path:
            return prs
        raise AssertionError(path)
    return read


class AlgoraCanonicalProbeTest(unittest.TestCase):
    def test_open_marketplace_closed_issue_prunes_before_expensive_pr_reads(self):
        calls = []
        upstream = provider(state="closed")
        def read(path):
            calls.append(path)
            return upstream(path)
        receipt = inspect_algora_listing(listing(), reader=read)
        self.assertEqual(receipt["disposition"], "PRUNE")
        self.assertIn("CANONICAL_ISSUE_CLOSED", receipt["reason_codes"])
        self.assertFalse(any("/pulls?" in path for path in calls))
        self.assertIsNone(receipt["economics"]["verified_received_usd"])

    def test_archived_and_forked_targets_are_pruned(self):
        for condition in ({"archived": True}, {"fork": True}):
            receipt = inspect_algora_listing(listing(), reader=provider(**condition))
            self.assertEqual(receipt["disposition"], "PRUNE")
            self.assertEqual(receipt["canonical"]["pr_pages_scanned"], 0)

    def test_concurrent_open_pr_reference_is_visible_and_blocks_admission(self):
        pr = {"number": 42, "title": "Fixes #6674", "body": "Bounty resolution",
              "html_url": "https://github.com/projectdiscovery/nuclei/pull/42",
              "user": {"login": "woahwhattheheck"}}
        receipt = inspect_algora_listing(listing(), reader=provider(prs=[pr]))
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertEqual(receipt["canonical"]["linked_prs"][0]["number"], 42)
        self.assertFalse(receipt["authority"]["build"])

    def test_qualified_same_repo_refs_and_numeric_prefixes(self):
        related = [
            "Fixes projectdiscovery/nuclei#6674",
            "Fixes PROJECTDISCOVERY/NUCLEI#6674",
            "Fixes https://github.com/projectdiscovery/nuclei/issues/6674",
            "Fixes https://github.com/projectdiscovery/nuclei/issues/6674#comment",
        ]
        unrelated = [
            "Fixes someoneelse/nuclei#6674",
            "Fixes projectdiscovery/otherrepo#6674",
            "Fixes someone/projectdiscovery/nuclei#6674",
            "Fixes https://github.com/projectdiscovery/nuclei/issues/66740",
            "Fixes https://github.com/projectdiscovery/nuclei/issues/6674abc",
            "Fixes https://github.com/otherowner/nuclei/issues/6674",
        ]
        for reference in related + unrelated:
            pr = {"number": 75, "title": reference, "body": "",
                  "html_url": "https://github.com/projectdiscovery/nuclei/pull/75",
                  "user": {"login": "otherdev"}}
            receipt = inspect_algora_listing(listing(), reader=provider(prs=[pr]))
            actual = "OPEN_PR_REFERENCE_PRESENT" in receipt["reason_codes"]
            self.assertEqual(actual, reference in related, reference)
            self.assertEqual(len(receipt["canonical"]["linked_prs"]),
                             1 if reference in related else 0)

    def test_incomplete_pr_pages_and_failed_provider_reads_hold(self):
        one_hundred = [{"number": i + 1, "title": "another change", "body": ""}
                       for i in range(100)]
        receipt = inspect_algora_listing(
            listing(), reader=provider(prs=one_hundred), max_pr_pages=1
        )
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("PR_CENSUS_TRUNCATED", receipt["reason_codes"])
        invalid = inspect_algora_listing(listing(), reader=lambda path: {})
        self.assertIn("CANONICAL_READ_FAILED", invalid["reason_codes"])

    def test_clean_injected_evidence_cannot_create_claim_authority(self):
        receipt = inspect_algora_listing(listing(), reader=provider())
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("FIXTURE_NOT_LIVE_PROVIDER", receipt["reason_codes"])
        self.assertEqual(receipt["authority"], {
            "claim": False, "build": False, "publish": False, "payment": False,
        })


if __name__ == "__main__":
    unittest.main()
