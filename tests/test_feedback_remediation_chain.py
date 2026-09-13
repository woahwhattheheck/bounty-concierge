import copy
import unittest
from io import BytesIO
from unittest.mock import patch

from concierge.feedback_remediation import (
    FeedbackRemediationError,
    compile_remediation,
    advance_remediation,
    strict_json_loads,
    verify_chain,
    verify_packet,
    digest,
    event_fp,
    obligation_id,
    load_json,
    MAX_JSON_BYTES,
)
from tests import test_feedback_remediation as fixtures


class FeedbackRemediationChainTests(unittest.TestCase):
    def fixture(self):
        return fixtures.FeedbackRemediationTests()

    @staticmethod
    def reseal(packet):
        packet["packet_digest"] = ""
        packet["packet_digest"] = digest(packet)
        return packet

    def test_chain_rejects_dropped_prior_obligation(self):
        f = self.fixture()
        prior = compile_remediation(f.compile_input())
        successor = advance_remediation(f.advance_input(prior))
        successor["obligations"] = []
        successor["counts"] = {
            "total": 0, "open": 0, "addressed": 0,
            "open_required": 0, "open_clarify": 0, "open_non_blocking": 0,
        }
        self.reseal(successor)
        self.assertEqual(verify_packet(successor), successor)
        with self.assertRaises(FeedbackRemediationError):
            verify_chain([prior, successor])

    def test_chain_rejects_rewritten_prior_obligation(self):
        f = self.fixture()
        prior = compile_remediation(f.compile_input())
        successor = advance_remediation(f.advance_input(prior))
        item = successor["obligations"][0]
        item["summary"] = "rewritten history"
        event = {
            key: item[key]
            for key in (
                "source_event_id", "authority", "kind", "occurred_at",
                "evidence_digest", "summary", "disposition",
                "target_head_sha", "target_artifact_revision", "hints",
            )
        }
        item["event_fingerprint"] = event_fp(event)
        item["obligation_id"] = obligation_id(
            successor["submission"]["canonical_source"], event
        )
        self.reseal(successor)
        self.assertEqual(verify_packet(successor), successor)
        with self.assertRaises(FeedbackRemediationError):
            verify_chain([prior, successor])

    def test_chain_rejects_backdated_injected_obligation(self):
        f = self.fixture()
        prior = compile_remediation(f.compile_input())
        successor = advance_remediation(f.advance_input(prior))
        injected = compile_remediation(
            f.compile_input([
                f.event(
                    event_id="review-102",
                    evidence="7",
                    summary="Second old-generation obligation.",
                )
            ])
        )["obligations"][0]
        successor["obligations"].append(injected)
        successor["obligations"].sort(
            key=lambda item: (
                item["target_artifact_revision"],
                item["occurred_at"],
                item["source_event_id"],
                item["obligation_id"],
            )
        )
        successor["counts"] = {
            "total": 2, "open": 2, "addressed": 0,
            "open_required": 2, "open_clarify": 0, "open_non_blocking": 0,
        }
        self.reseal(successor)
        self.assertEqual(verify_packet(successor), successor)
        with self.assertRaises(FeedbackRemediationError):
            verify_chain([prior, successor])

    def test_strict_json_rejects_huge_integer_before_python_conversion(self):
        with self.assertRaises(FeedbackRemediationError):
            strict_json_loads('{"x":' + ("9" * 10_000) + "}")


    def test_load_json_bounds_the_read_itself(self):
        class GuardedReader(BytesIO):
            def __init__(self, payload):
                super().__init__(payload)
                self.read_sizes = []

            def read(self, size=-1):
                self.read_sizes.append(size)
                if size < 0:
                    raise AssertionError("unbounded read")
                return super().read(size)

        reader = GuardedReader(b"x" * (MAX_JSON_BYTES + 1))
        with patch("concierge.feedback_remediation.Path.open", return_value=reader):
            with self.assertRaises(FeedbackRemediationError):
                load_json("ignored.json")
        self.assertEqual(reader.read_sizes, [MAX_JSON_BYTES + 1])

    def test_non_normalized_hint_path_rejected(self):
        f = self.fixture()
        value = f.compile_input()
        value["feedback"][0]["hints"] = [
            {"path": "src//client.py", "line_start": 1, "line_end": 1}
        ]
        with self.assertRaises(FeedbackRemediationError):
            compile_remediation(value)


if __name__ == "__main__":
    unittest.main()
