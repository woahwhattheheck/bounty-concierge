# SPDX-License-Identifier: MIT
"""Atomic Git-ref coordination for cooperative cross-session outbound writers.

Slack/search feeds are useful audit surfaces but are not a mutex: indexed search can
lag behind acknowledged writes.  This module uses GitHub's atomic ref creation and
non-force fast-forward update rules as the shared serialization point.  A branch is
created per SHA-256 outbound operation key; every state transition is a child commit
of the exact prior state.  Competing siblings cannot both advance the ref.
"""
from __future__ import annotations

import json
import re
import uuid
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import quote

import requests

STATE_PREFIX = "OUTBOUND_GITREF_STATE_V1\n"
REF_PREFIX = "outbound-claims/"
_KEY = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED = {"CLAIMED", "RELEASED", "SENT"}


class GitRefClaimError(RuntimeError):
    pass


class ClaimUnavailable(GitRefClaimError):
    pass


class ClaimCorrupt(GitRefClaimError):
    pass


class ClaimBusy(GitRefClaimError):
    pass


class ClaimSent(GitRefClaimError):
    pass


class ClaimLost(GitRefClaimError):
    pass


class ClaimConflict(GitRefClaimError):
    pass


@dataclass(frozen=True)
class GitRefClaimState:
    operation_key: str
    status: str
    owner: str
    claim_id: str
    sequence: int
    provider_receipt_id: Optional[str] = None
    evidence_reference: Optional[str] = None

    def payload(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "evidence_reference": self.evidence_reference,
            "operation_key": self.operation_key,
            "owner": self.owner,
            "provider_receipt_id": self.provider_receipt_id,
            "sequence": self.sequence,
            "status": self.status,
        }


@dataclass(frozen=True)
class GitRefClaimReceipt:
    commit_sha: str
    ref_name: str
    state: GitRefClaimState


def _text(name: str, value: Any, limit: int = 512) -> str:
    if not isinstance(value, str):
        raise GitRefClaimError(f"{name} must be a string")
    value = value.strip()
    if not value or len(value) > limit or any(unicodedata.category(ch).startswith("C") for ch in value):
        raise GitRefClaimError(f"{name} is empty, too long, or contains controls")
    return value


def _key(value: str) -> str:
    value = _text("operation_key", value, 64)
    if not _KEY.fullmatch(value):
        raise GitRefClaimError("operation_key must be lowercase SHA-256 hex")
    return value


def ref_name(operation_key: str) -> str:
    return REF_PREFIX + _key(operation_key)


def encode_state(state: GitRefClaimState) -> str:
    validate_state(state)
    return STATE_PREFIX + json.dumps(state.payload(), sort_keys=True, separators=(",", ":"))


def decode_state(message: Any) -> GitRefClaimState:
    if not isinstance(message, str) or not message.startswith(STATE_PREFIX):
        raise ClaimCorrupt("claim commit does not contain an outbound state record")
    try:
        raw = json.loads(message[len(STATE_PREFIX):])
    except json.JSONDecodeError as exc:
        raise ClaimCorrupt("claim state JSON is malformed") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "claim_id", "evidence_reference", "operation_key", "owner",
        "provider_receipt_id", "sequence", "status",
    }:
        raise ClaimCorrupt("claim state schema is malformed")
    try:
        state = GitRefClaimState(**raw)
        validate_state(state)
    except GitRefClaimError as exc:
        raise ClaimCorrupt(str(exc)) from exc
    return state


def validate_state(state: GitRefClaimState) -> None:
    _key(state.operation_key)
    _text("owner", state.owner, 256)
    _text("claim_id", state.claim_id, 256)
    if state.status not in _ALLOWED:
        raise GitRefClaimError("invalid claim status")
    if isinstance(state.sequence, bool) or not isinstance(state.sequence, int) or state.sequence < 1:
        raise GitRefClaimError("sequence must be a positive integer")
    if state.provider_receipt_id is not None:
        _text("provider_receipt_id", state.provider_receipt_id)
    if state.evidence_reference is not None:
        _text("evidence_reference", state.evidence_reference, 1024)
    if state.status == "SENT" and state.provider_receipt_id is None:
        raise GitRefClaimError("SENT requires provider_receipt_id")
    if state.status != "SENT" and state.provider_receipt_id is not None:
        raise GitRefClaimError("only SENT may carry provider_receipt_id")


class GitHubRefClaimStore:
    """GitHub REST backed compare-and-swap claim store for cooperative writers.

    The caller supplies a token with write access to ``repo``.  No destination or
    message body is stored: ref names contain only the operation SHA-256 key and
    state commits contain owner/claim ids plus optional opaque receipt evidence.
    """

    def __init__(self, repo: str, token: str, *, base_branch: str = "main",
                 session: Optional[Any] = None, api_base: str = "https://api.github.com",
                 timeout: int = 15) -> None:
        if not isinstance(repo, str) or repo.count("/") != 1:
            raise GitRefClaimError("repo must be owner/name")
        self.repo = repo
        self.token = _text("token", token, 4096)
        self.base_branch = _text("base_branch", base_branch, 256)
        self.session = session or requests.Session()
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    @property
    def _root(self) -> str:
        return f"{self.api_base}/repos/{self.repo}"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, path: str, *, body: Optional[Mapping[str, Any]] = None):
        try:
            return self.session.request(method, self._root + path, headers=self._headers(),
                                        json=dict(body) if body is not None else None,
                                        timeout=self.timeout)
        except requests.RequestException as exc:
            raise ClaimUnavailable("GitHub claim authority is unavailable") from exc

    @staticmethod
    def _data(response: Any) -> dict[str, Any]:
        try:
            value = response.json()
        except Exception as exc:
            raise ClaimUnavailable("GitHub returned a non-JSON claim response") from exc
        if not isinstance(value, dict):
            raise ClaimUnavailable("GitHub returned malformed claim response")
        return value

    def _ref_path(self, key: str) -> str:
        return "/git/ref/" + quote("heads/" + ref_name(key), safe="/")

    def _ref_update_path(self, key: str) -> str:
        return "/git/refs/" + quote("heads/" + ref_name(key), safe="/")

    def _read_ref_sha(self, key: str) -> Optional[str]:
        response = self._request("GET", self._ref_path(key))
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ClaimUnavailable(f"GitHub ref read failed with HTTP {response.status_code}")
        data = self._data(response)
        sha = data.get("object", {}).get("sha") if isinstance(data.get("object"), dict) else None
        if not isinstance(sha, str) or len(sha) != 40:
            raise ClaimCorrupt("claim ref does not target a commit SHA")
        return sha

    def _read_commit(self, sha: str) -> tuple[GitRefClaimState, str]:
        response = self._request("GET", f"/git/commits/{quote(sha, safe='')}")
        if response.status_code != 200:
            raise ClaimUnavailable(f"GitHub claim commit read failed with HTTP {response.status_code}")
        data = self._data(response)
        tree = data.get("tree")
        tree_sha = tree.get("sha") if isinstance(tree, dict) else None
        if not isinstance(tree_sha, str) or len(tree_sha) != 40:
            raise ClaimCorrupt("claim commit tree is malformed")
        return decode_state(data.get("message")), tree_sha

    def inspect(self, operation_key: str) -> Optional[GitRefClaimReceipt]:
        key = _key(operation_key)
        sha = self._read_ref_sha(key)
        if sha is None:
            return None
        state, _ = self._read_commit(sha)
        if state.operation_key != key:
            raise ClaimCorrupt("claim ref key and state key disagree")
        return GitRefClaimReceipt(sha, ref_name(key), state)

    def _anchor(self) -> tuple[str, str]:
        response = self._request("GET", "/git/ref/" + quote("heads/" + self.base_branch, safe="/"))
        if response.status_code != 200:
            raise ClaimUnavailable(f"base ref read failed with HTTP {response.status_code}")
        data = self._data(response)
        sha = data.get("object", {}).get("sha") if isinstance(data.get("object"), dict) else None
        if not isinstance(sha, str) or len(sha) != 40:
            raise ClaimCorrupt("base ref commit SHA is malformed")
        response = self._request("GET", f"/git/commits/{sha}")
        if response.status_code != 200:
            raise ClaimUnavailable(f"base commit read failed with HTTP {response.status_code}")
        commit = self._data(response)
        tree = commit.get("tree")
        tree_sha = tree.get("sha") if isinstance(tree, dict) else None
        if not isinstance(tree_sha, str) or len(tree_sha) != 40:
            raise ClaimCorrupt("base commit tree is malformed")
        return sha, tree_sha

    def _create_commit(self, state: GitRefClaimState, parent_sha: str, tree_sha: str) -> str:
        response = self._request("POST", "/git/commits", body={
            "message": encode_state(state), "tree": tree_sha, "parents": [parent_sha],
        })
        if response.status_code != 201:
            raise ClaimUnavailable(f"claim commit creation failed with HTTP {response.status_code}")
        sha = self._data(response).get("sha")
        if not isinstance(sha, str) or len(sha) != 40:
            raise ClaimCorrupt("GitHub returned malformed claim commit SHA")
        return sha

    def _advance(self, current: GitRefClaimReceipt, desired: GitRefClaimState) -> GitRefClaimReceipt:
        _, tree_sha = self._read_commit(current.commit_sha)
        candidate = self._create_commit(desired, current.commit_sha, tree_sha)
        response = self._request("PATCH", self._ref_update_path(desired.operation_key),
                                 body={"sha": candidate, "force": False})
        if response.status_code == 200:
            return GitRefClaimReceipt(candidate, ref_name(desired.operation_key), desired)
        if response.status_code in {409, 422}:
            latest = self.inspect(desired.operation_key)
            if latest and latest.state == desired:
                return latest
            raise ClaimConflict("another writer advanced the claim ref first")
        raise ClaimUnavailable(f"claim ref update failed with HTTP {response.status_code}")

    def claim(self, operation_key: str, owner: str, *, claim_id: Optional[str] = None) -> GitRefClaimReceipt:
        key, owner = _key(operation_key), _text("owner", owner, 256)
        claim_id = _text("claim_id", claim_id or uuid.uuid4().hex, 256)
        current = self.inspect(key)
        if current is None:
            parent, tree = self._anchor()
            desired = GitRefClaimState(key, "CLAIMED", owner, claim_id, 1)
            candidate = self._create_commit(desired, parent, tree)
            response = self._request("POST", "/git/refs", body={
                "ref": "refs/heads/" + ref_name(key), "sha": candidate,
            })
            if response.status_code == 201:
                return GitRefClaimReceipt(candidate, ref_name(key), desired)
            if response.status_code != 422:
                raise ClaimUnavailable(f"claim ref creation failed with HTTP {response.status_code}")
            current = self.inspect(key)
            if current is None:
                raise ClaimUnavailable("GitHub rejected claim ref creation without visible winner")
        state = current.state
        if state.status == "SENT":
            raise ClaimSent("operation already has terminal SENT authority")
        if state.status == "CLAIMED":
            if state.owner == owner and state.claim_id == claim_id:
                return current
            raise ClaimBusy(f"operation is claimed by {state.owner}")
        desired = GitRefClaimState(key, "CLAIMED", owner, claim_id, state.sequence + 1)
        return self._advance(current, desired)

    def release(self, operation_key: str, owner: str, claim_id: str, *,
                evidence_reference: Optional[str] = None) -> GitRefClaimReceipt:
        key, owner, claim_id = _key(operation_key), _text("owner", owner, 256), _text("claim_id", claim_id, 256)
        current = self.inspect(key)
        if current is None:
            raise ClaimLost("claim ref is absent")
        state = current.state
        if state.status == "RELEASED" and state.owner == owner and state.claim_id == claim_id:
            return current
        if state.status != "CLAIMED" or state.owner != owner or state.claim_id != claim_id:
            raise ClaimLost("claim generation no longer owns the operation")
        evidence = _text("evidence_reference", evidence_reference, 1024) if evidence_reference else None
        desired = GitRefClaimState(key, "RELEASED", owner, claim_id, state.sequence + 1,
                                   evidence_reference=evidence)
        return self._advance(current, desired)

    def mark_sent(self, operation_key: str, owner: str, claim_id: str,
                  provider_receipt_id: str) -> GitRefClaimReceipt:
        key, owner, claim_id = _key(operation_key), _text("owner", owner, 256), _text("claim_id", claim_id, 256)
        receipt = _text("provider_receipt_id", provider_receipt_id)
        current = self.inspect(key)
        if current is None:
            raise ClaimLost("claim ref is absent")
        state = current.state
        if state.status == "SENT":
            if state.owner == owner and state.claim_id == claim_id and state.provider_receipt_id == receipt:
                return current
            raise ClaimSent("operation already finalized by another receipt/generation")
        if state.status != "CLAIMED" or state.owner != owner or state.claim_id != claim_id:
            raise ClaimLost("claim generation no longer owns the operation")
        desired = GitRefClaimState(key, "SENT", owner, claim_id, state.sequence + 1,
                                   provider_receipt_id=receipt)
        return self._advance(current, desired)
