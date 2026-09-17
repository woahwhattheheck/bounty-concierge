# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import unittest

from concierge.accepted_work_collection_bridge import AcceptedWorkBridgeError, build_bridge
from tests.test_accepted_work_collection_bridge import (
    AS_OF,
    FRESHNESS,
    KEY,
    acceptance_manifest,
    fixture_doc,
    signed_registry,
)


class AcceptedWorkSourceBindingTests(unittest.TestCase):
    def test_existing_settlement_source_cannot_be_relabeled_as_acceptance(self):
        doc = fixture_doc("synthetic-award-ticket-rail")
        acceptance = acceptance_manifest(doc)
        award = next(
            event
            for event in doc["cases"][0]["events"]
            if event["kind"] == "SPONSOR_AWARD"
        )
        acceptance["records"][0]["source"] = copy.deepcopy(award["source"])
        registry = signed_registry(doc, acceptance)
        with self.assertRaises(AcceptedWorkBridgeError):
            build_bridge(
                doc,
                registry,
                KEY,
                acceptance,
                as_of=AS_OF,
                freshness_seconds=FRESHNESS,
            )

    def test_dedicated_acceptance_source_still_composes(self):
        doc = fixture_doc("synthetic-award-ticket-rail")
        acceptance = acceptance_manifest(doc)
        registry = signed_registry(doc, acceptance)
        bridge = build_bridge(
            doc,
            registry,
            KEY,
            acceptance,
            as_of=AS_OF,
            freshness_seconds=FRESHNESS,
        )
        self.assertEqual(
            bridge["records"][0]["finish_state"],
            "ACCEPTED_UNPAID_COLLECTIONS_REVIEW",
        )


if __name__ == "__main__":
    unittest.main()
