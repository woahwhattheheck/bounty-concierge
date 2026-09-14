# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import json
import unittest

from concierge.outbound_capability_gate import (
    CANONICAL_LEASE_REPO,
    LOCAL_PREFLIGHT_SCHEMA,
    CapabilityGateBlocked,
    _prepare_send_with_transport,
    _verify_possession_bound,
    build_commons_v2_claim,
    local_preflight_sha256,
)

T0 = "2026-09-14T09:30:00Z"
ANCHOR = "1" * 40
OPKEY = "2" * 64
UPSTREAM = "3" * 64
TAG_SHA = "4" * 40
CAPABILITY = "5" * 64
COMMITMENT = hashlib.sha256(bytes.fromhex(CAPABILITY)).hexdigest()


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def digest(value):
    return hashlib.sha256(canon(value)).hexdigest()


def claim():
    return build_commons_v2_claim(
        buyer_scope="example.test",
        offer_scope="lims-pilot",
        claimant="ZAM-P7X3",
        claim_id="zam-p7x3-example-lims-20260914",
        claim_started_at=T0,
        anchor_sha=ANCHOR,
        operation_key=OPKEY,
        outbound_preflight_sha256=UPSTREAM,
    )


def receipt(c, *, held=True):
    seam = {
        "schema": "outbound-send-lease/v2",
        "buyer_scope": c["buyer_scope"],
        "offer_scope": c["offer_scope"],
    }
    seam_sha = digest(seam)
    out = {
        "schema": "outbound-send-lease-receipt/v2",
        "repo": c["repo"],
        "buyer_scope": c["buyer_scope"],
        "offer_scope": c["offer_scope"],
        "seam_sha256": seam_sha,
        "lease_ref": "refs/tags/outbound-lease-v2/" + seam_sha,
        "claim_id": c["claim_id"],
        "claimant": c["claimant"],
        "claim_started_at": c["claim_started_at"],
        "anchor_sha": c["anchor_sha"],
        "preflight_sha256": c["preflight_sha256"],
        "claim_capability_sha256": COMMITMENT,
        "tag_object_sha": TAG_SHA,
        "observed_ref_sha": TAG_SHA if held else None,
        "lease_held_by_claimant": held,
        "decision": "LEASE_HELD" if held else "HOLD",
        "reason": "ACQUIRED_CREATE_201" if held else "ACQUIRE_OUTCOME_UNPROVEN",
        "external_send_authorized": False,
    }
    out["receipt_sha256"] = digest(out)
    return out


def provider(c, r):
    seam = r["seam_sha256"]
    claimant_hash = hashlib.sha256(c["claimant"].encode("ascii")).hexdigest()[:16]
    metadata = {
        "schema": "outbound-send-lease/v2",
        "repo": c["repo"],
        "buyer_scope": c["buyer_scope"],
        "offer_scope": c["offer_scope"],
        "claimant": c["claimant"],
        "claim_id": c["claim_id"],
        "claim_started_at": c["claim_started_at"],
        "anchor_sha": c["anchor_sha"],
        "preflight_sha256": c["preflight_sha256"],
        "claim_capability_sha256": COMMITMENT,
        "seam_sha256": seam,
    }
    ref = {"ref": r["lease_ref"], "object": {"type": "tag", "sha": TAG_SHA}}
    tag = {
        "sha": TAG_SHA,
        "tag": "outbound-claim-v2-%s-%s-%s" % (
            seam[:16], claimant_hash, COMMITMENT[:16]),
        "message": canon(metadata).decode("ascii") + "\n",
        "tagger": {
            "name": "outbound-send-lease-v2",
            "email": "lease@tokenjunkielabs.invalid",
            "date": c["claim_started_at"],
        },
        "object": {"type": "commit", "sha": c["anchor_sha"]},
    }
    return ref, tag


class FakeTransport:
    def __init__(self, ref, tag, *, ref_status=200, tag_status=200):
        self.ref = ref
        self.tag = tag
        self.ref_status = ref_status
        self.tag_status = tag_status
        self.calls = []

    def __call__(self, method, path, payload):
        self.calls.append((method, path, payload))
        if "/git/ref/" in path:
            return self.ref_status, self.ref
        if "/git/tags/" in path:
            return self.tag_status, self.tag
        raise AssertionError(path)


class FakeGuard:
    def __init__(self):
        self.calls = []

    def prepare_send(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "SENDING", "permit_id": "permit-1", **kwargs}


class CapabilityGateTests(unittest.TestCase):
    def setUp(self):
        self.c = claim()
        self.r = receipt(self.c)
        self.ref, self.tag = provider(self.c, self.r)

    def transport(self):
        return FakeTransport(self.ref, self.tag)

    def verify(self, **overrides):
        args = dict(
            operation_key=OPKEY,
            outbound_preflight_sha256=UPSTREAM,
            owner="ZAM-P7X3",
            claim=self.c,
            lease_receipt=self.r,
            claim_capability=CAPABILITY,
            transport=self.transport(),
        )
        args.update(overrides)
        return _verify_possession_bound(**args)

    def test_claim_repo_is_pinned_and_preflight_binds_local_generation(self):
        self.assertEqual(CANONICAL_LEASE_REPO, self.c["repo"])
        expected = digest({
            "schema": LOCAL_PREFLIGHT_SCHEMA,
            "operation_key": OPKEY,
            "outbound_preflight_sha256": UPSTREAM,
        })
        self.assertEqual(expected, local_preflight_sha256(
            operation_key=OPKEY, outbound_preflight_sha256=UPSTREAM))
        self.assertEqual(expected, self.c["preflight_sha256"])

    def test_exact_holder_live_ref_and_tag_prove_possession_not_send_authority(self):
        proof = self.verify()
        self.assertTrue(proof["proof_of_possession_verified"])
        self.assertTrue(proof["live_ref_verified"])
        self.assertTrue(proof["live_tag_verified"])
        self.assertFalse(proof["external_send_authorized"])
        self.assertNotIn(CAPABILITY, json.dumps(proof))

    def test_copied_public_winner_with_wrong_capability_blocks_before_provider_io(self):
        transport = self.transport()
        with self.assertRaises(CapabilityGateBlocked):
            self.verify(claim_capability="6" * 64, transport=transport)
        self.assertEqual([], transport.calls)

    def test_malformed_capability_blocks_before_provider_io(self):
        transport = self.transport()
        with self.assertRaises(Exception):
            self.verify(claim_capability="not-secret", transport=transport)
        self.assertEqual([], transport.calls)

    def test_noncanonical_repo_blocks_before_provider_io(self):
        bad = dict(self.c); bad["repo"] = "attacker/split-brain"
        transport = self.transport()
        with self.assertRaises(CapabilityGateBlocked):
            self.verify(claim=bad, transport=transport)
        self.assertEqual([], transport.calls)

    def test_changed_local_operation_blocks_before_provider_io(self):
        transport = self.transport()
        with self.assertRaises(CapabilityGateBlocked):
            self.verify(operation_key="7" * 64, transport=transport)
        self.assertEqual([], transport.calls)

    def test_changed_upstream_preflight_blocks_before_provider_io(self):
        transport = self.transport()
        with self.assertRaises(CapabilityGateBlocked):
            self.verify(outbound_preflight_sha256="8" * 64, transport=transport)
        self.assertEqual([], transport.calls)

    def test_claimant_must_match_local_owner_before_provider_io(self):
        transport = self.transport()
        with self.assertRaises(CapabilityGateBlocked):
            self.verify(owner="OTHER-OWNER", transport=transport)
        self.assertEqual([], transport.calls)

    def test_receipt_digest_tamper_blocks_before_provider_io(self):
        bad = dict(self.r); bad["reason"] = "tampered"
        transport = self.transport()
        with self.assertRaises(CapabilityGateBlocked):
            self.verify(lease_receipt=bad, transport=transport)
        self.assertEqual([], transport.calls)

    def test_hold_receipt_can_recover_only_from_exact_live_possession(self):
        hold = receipt(self.c, held=False)
        ref, tag = provider(self.c, hold)
        proof = self.verify(lease_receipt=hold, transport=FakeTransport(ref, tag))
        self.assertTrue(proof["proof_of_possession_verified"])

    def test_live_ref_drift_blocks(self):
        ref = dict(self.ref); ref["object"] = {"type": "tag", "sha": "9" * 40}
        with self.assertRaises(CapabilityGateBlocked):
            self.verify(transport=FakeTransport(ref, self.tag))

    def test_live_ref_must_point_to_annotated_tag(self):
        ref = dict(self.ref); ref["object"] = {"type": "commit", "sha": TAG_SHA}
        with self.assertRaises(CapabilityGateBlocked):
            self.verify(transport=FakeTransport(ref, self.tag))

    def test_ref_read_failure_stops_before_tag_read(self):
        t = FakeTransport(self.ref, self.tag, ref_status=503)
        with self.assertRaises(CapabilityGateBlocked):
            self.verify(transport=t)
        self.assertEqual(1, len(t.calls))

    def test_metadata_transplant_blocks(self):
        tag = dict(self.tag)
        tag["message"] = tag["message"].replace("lims-pilot", "other-offer")
        with self.assertRaises(CapabilityGateBlocked):
            self.verify(transport=FakeTransport(self.ref, tag))

    def test_duplicate_metadata_key_blocks(self):
        tag = dict(self.tag)
        tag["message"] = tag["message"].replace(
            '"schema":"outbound-send-lease/v2"',
            '"schema":"outbound-send-lease/v2","schema":"outbound-send-lease/v2"',
        )
        with self.assertRaises(CapabilityGateBlocked):
            self.verify(transport=FakeTransport(self.ref, tag))

    def test_anchor_target_drift_blocks(self):
        tag = dict(self.tag); tag["object"] = {"type": "commit", "sha": "a" * 40}
        with self.assertRaises(CapabilityGateBlocked):
            self.verify(transport=FakeTransport(self.ref, tag))

    def test_capability_commitment_transplant_blocks(self):
        tag = dict(self.tag)
        metadata = json.loads(tag["message"])
        metadata["claim_capability_sha256"] = "a" * 64
        tag["message"] = canon(metadata).decode("ascii") + "\n"
        with self.assertRaises(CapabilityGateBlocked):
            self.verify(transport=FakeTransport(self.ref, tag))

    def test_tagger_identity_drift_blocks(self):
        tag = dict(self.tag); tag["tagger"] = dict(tag["tagger"], name="other")
        with self.assertRaises(CapabilityGateBlocked):
            self.verify(transport=FakeTransport(self.ref, tag))

    def test_valid_proof_precedes_exact_local_transition_without_caller_clock(self):
        guard = FakeGuard()
        result = _prepare_send_with_transport(
            guard,
            operation_key=OPKEY,
            lease_id="lease-1",
            owner="ZAM-P7X3",
            outbound_preflight_sha256=UPSTREAM,
            claim=self.c,
            lease_receipt=self.r,
            claim_capability=CAPABILITY,
            transport=self.transport(),
        )
        self.assertEqual("SENDING", result["send_state"]["status"])
        self.assertFalse(result["external_send_authorized"])
        self.assertEqual([{
            "operation_key": OPKEY,
            "lease_id": "lease-1",
            "owner": "ZAM-P7X3",
        }], guard.calls)

    def test_invalid_proof_never_calls_local_prepare_send(self):
        guard = FakeGuard()
        with self.assertRaises(CapabilityGateBlocked):
            _prepare_send_with_transport(
                guard,
                operation_key=OPKEY,
                lease_id="lease-1",
                owner="ZAM-P7X3",
                outbound_preflight_sha256=UPSTREAM,
                claim=self.c,
                lease_receipt=self.r,
                claim_capability="6" * 64,
                transport=self.transport(),
            )
        self.assertEqual([], guard.calls)


if __name__ == "__main__":
    unittest.main()
