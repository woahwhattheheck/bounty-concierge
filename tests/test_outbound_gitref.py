# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import unittest
from urllib.parse import unquote

from concierge.outbound_gitref import (
    ClaimBusy, ClaimConflict, ClaimCorrupt, ClaimLost, ClaimSent,
    GitHubRefClaimStore, decode_state, encode_state, ref_name,
)


def sha(label):
    return hashlib.sha1(label.encode()).hexdigest()


class Response:
    def __init__(self, code, data): self.status_code, self._data = code, data
    def json(self): return self._data


class FakeGitHub:
    def __init__(self):
        self.refs = {"heads/main": sha("main")}
        self.commits = {sha("main"): {"message": "base", "tree": {"sha": sha("tree")}, "parents": []}}
        self.counter = 0
        self.conflict_next_patch = False

    def request(self, method, url, headers=None, json=None, timeout=None):
        path = url.split("/repos/o/r", 1)[1]
        if method == "GET" and path.startswith("/git/ref/"):
            ref = unquote(path[len("/git/ref/"):])
            if ref not in self.refs: return Response(404, {"message": "Not Found"})
            return Response(200, {"ref": "refs/" + ref, "object": {"sha": self.refs[ref]}})
        if method == "GET" and path.startswith("/git/commits/"):
            commit = self.commits.get(path.rsplit("/", 1)[1])
            return Response(200, commit) if commit else Response(404, {})
        if method == "POST" and path == "/git/commits":
            self.counter += 1; new = sha("commit-%d" % self.counter)
            self.commits[new] = {"message": json["message"], "tree": {"sha": json["tree"]}, "parents": list(json["parents"])}
            return Response(201, {"sha": new})
        if method == "POST" and path == "/git/refs":
            ref = json["ref"].removeprefix("refs/")
            if ref in self.refs: return Response(422, {"message": "Reference already exists"})
            self.refs[ref] = json["sha"]; return Response(201, {"ref": json["ref"]})
        if method == "PATCH" and path.startswith("/git/refs/"):
            ref = unquote(path[len("/git/refs/"):])
            if ref not in self.refs: return Response(422, {})
            candidate = json["sha"]
            if self.conflict_next_patch:
                self.conflict_next_patch = False
                current = self.refs[ref]
                self.counter += 1; sibling = sha("racer-%d" % self.counter)
                self.commits[sibling] = {"message": self.commits[current]["message"], "tree": self.commits[candidate]["tree"], "parents": [current]}
                self.refs[ref] = sibling
                return Response(422, {"message": "not a fast forward"})
            current = self.refs[ref]
            if current not in self.commits[candidate]["parents"]: return Response(422, {"message": "not a fast forward"})
            self.refs[ref] = candidate; return Response(200, {"ref": "refs/" + ref})
        return Response(500, {"message": "unexpected"})


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitHub()
        self.a = GitHubRefClaimStore("o/r", "token", session=self.fake, api_base="https://x")
        self.b = GitHubRefClaimStore("o/r", "token", session=self.fake, api_base="https://x")
        self.key = "ab" * 32

    def test_ref_is_deterministic_and_contains_no_destination(self):
        self.assertEqual(ref_name(self.key), "outbound-claims/" + self.key)
        self.assertNotIn("@", ref_name(self.key))

    def test_first_ref_creator_wins(self):
        first = self.a.claim(self.key, "seat-a", claim_id="a1")
        self.assertEqual(first.state.status, "CLAIMED")
        with self.assertRaises(ClaimBusy): self.b.claim(self.key, "seat-b", claim_id="b1")
        self.assertEqual(self.a.claim(self.key, "seat-a", claim_id="a1"), first)

    def test_release_then_new_claim_is_parent_linked_handoff(self):
        first = self.a.claim(self.key, "seat-a", claim_id="a1")
        released = self.a.release(self.key, "seat-a", "a1", evidence_reference="operator-handoff:1")
        second = self.b.claim(self.key, "seat-b", claim_id="b1")
        self.assertEqual((first.state.sequence, released.state.sequence, second.state.sequence), (1, 2, 3))
        self.assertEqual(second.state.owner, "seat-b")

    def test_stale_generation_cannot_send_after_handoff(self):
        self.a.claim(self.key, "seat-a", claim_id="a1")
        self.a.release(self.key, "seat-a", "a1")
        self.b.claim(self.key, "seat-b", claim_id="b1")
        with self.assertRaises(ClaimLost): self.a.mark_sent(self.key, "seat-a", "a1", "msg-old")

    def test_sent_is_terminal_and_idempotent_for_exact_receipt(self):
        self.a.claim(self.key, "seat-a", claim_id="a1")
        sent = self.a.mark_sent(self.key, "seat-a", "a1", "provider-msg-1")
        self.assertEqual(self.a.mark_sent(self.key, "seat-a", "a1", "provider-msg-1"), sent)
        with self.assertRaises(ClaimSent): self.a.mark_sent(self.key, "seat-a", "a1", "provider-msg-2")
        with self.assertRaises(ClaimSent): self.b.claim(self.key, "seat-b", claim_id="b1")

    def test_non_fast_forward_race_fails_closed(self):
        self.a.claim(self.key, "seat-a", claim_id="a1")
        self.fake.conflict_next_patch = True
        with self.assertRaises(ClaimConflict): self.a.release(self.key, "seat-a", "a1")

    def test_corrupt_state_commit_fails_closed(self):
        receipt = self.a.claim(self.key, "seat-a", claim_id="a1")
        self.fake.commits[receipt.commit_sha]["message"] = "not-state"
        with self.assertRaises(ClaimCorrupt): self.a.inspect(self.key)

    def test_codec_rejects_schema_tamper(self):
        receipt = self.a.claim(self.key, "seat-a", claim_id="a1")
        state = decode_state(self.fake.commits[receipt.commit_sha]["message"])
        self.assertEqual(decode_state(encode_state(state)), state)
        with self.assertRaises(ClaimCorrupt): decode_state("OUTBOUND_GITREF_STATE_V1\n{}")


if __name__ == "__main__": unittest.main()
