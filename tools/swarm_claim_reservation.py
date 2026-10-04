#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Atomic GitHub-backed reservations for swarm work custody.

Each work key maps to one deterministic branch. The first contender builds a
claim commit off the configured base branch and atomically creates that ref.
Later contenders must either observe an unexpired active lease or advance the
same ref with a non-force fast-forward after release/expiry. Concurrent
takeovers build sibling commits, so only one can fast-forward the ref.

This tool mutates only the configured custody repository. It does not post
upstream bounty claims, contact sponsors, submit work, or touch payment state.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
import re
import sys
from typing import Any
from urllib.parse import quote

import requests

SCHEMA = "swarm-custody-reservation/v1"
DEFAULT_REPO = "woahwhattheheck/bounty-concierge"
DEFAULT_BASE = "main"
STATE_PATH = ".swarm-custody-reservation-v1.json"
BRANCH_PREFIX = "swarm-custody/v1"
TOKEN_ENV = "GITHUB_TOKEN"
REPO_ENV = "SWARM_CUSTODY_REPO"
BASE_ENV = "SWARM_CUSTODY_BASE"
MAX_LEASE_SECONDS = 86_400
DEFAULT_LEASE_SECONDS = 900
_TIMEOUT_SECONDS = 15

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,127}$")
_EVENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}$")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")


class ReservationError(RuntimeError):
    """Stable operational error for the reservation CLI."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _time(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _parse_time(value: Any, field: str) -> datetime:
    if type(value) is not str or len(value) != 20 or not value.endswith("Z"):
        raise ReservationError("invalid_state", f"{field} is not canonical UTC")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ReservationError(
            "invalid_state", f"{field} is not canonical UTC"
        ) from exc
    if _time(parsed) != value:
        raise ReservationError("invalid_state", f"{field} is not canonical UTC")
    return parsed


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _work_key(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 512
        or any(ord(ch) < 32 for ch in value)
    ):
        raise ReservationError(
            "invalid_work_key", "work_key must be 1..512 printable characters"
        )
    return value


def _owner(value: str) -> str:
    if not isinstance(value, str) or _OWNER_RE.fullmatch(value) is None:
        raise ReservationError("invalid_owner", "owner is invalid")
    return value


def _event_id(value: str) -> str:
    if not isinstance(value, str) or _EVENT_RE.fullmatch(value) is None:
        raise ReservationError("invalid_event_id", "event_id is invalid")
    return value


def _repo(value: str) -> str:
    if not isinstance(value, str) or _REPO_RE.fullmatch(value) is None:
        raise ReservationError("invalid_repo", "repo must be owner/name")
    return value


def _artifact(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1000
        or any(ord(ch) < 32 for ch in value)
    ):
        raise ReservationError(
            "invalid_artifact", "artifact must be 1..1000 printable characters"
        )
    return value


def _lease_seconds(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_LEASE_SECONDS:
        raise ReservationError(
            "invalid_lease",
            f"lease_seconds must be an integer from 1 to {MAX_LEASE_SECONDS}",
        )
    return value


def _digest(work_key: str) -> str:
    return hashlib.sha256(work_key.encode("utf-8")).hexdigest()


def _branch(work_key: str) -> str:
    return f"{BRANCH_PREFIX}/{_digest(work_key)}"


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "bounty-concierge-swarm-reservation/1",
    }


class GitHub:
    def __init__(self, repo: str, token: str) -> None:
        self.repo = _repo(repo)
        if not token:
            raise ReservationError("missing_token", f"{TOKEN_ENV} is required")
        self.headers = _headers(token)
        self.base_url = f"https://api.github.com/repos/{self.repo}"

    def request(
        self,
        method: str,
        path: str,
        *,
        expected: set[int],
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> requests.Response:
        try:
            response = requests.request(
                method,
                self.base_url + path,
                headers=self.headers,
                json=json_body,
                params=params,
                timeout=_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise ReservationError(
                "provider_unreachable", "GitHub request failed"
            ) from exc
        if response.status_code in expected:
            return response
        detail = {
            "status": response.status_code,
            "request_id": response.headers.get("X-GitHub-Request-Id"),
            "retry_after": response.headers.get("Retry-After"),
            "rate_limit_remaining": response.headers.get(
                "X-RateLimit-Remaining"
            ),
            "rate_limit_reset": response.headers.get("X-RateLimit-Reset"),
        }
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("message"), str):
            detail["provider_message"] = payload["message"]
        message = str(
            detail.get("provider_message")
            or f"GitHub HTTP {response.status_code}"
        )
        rate_limited = response.status_code == 429
        if response.status_code == 403:
            remaining = response.headers.get("X-RateLimit-Remaining")
            remaining_exhausted = False
            if isinstance(remaining, str):
                try:
                    remaining_exhausted = int(remaining.strip()) == 0
                except ValueError:
                    pass

            retry_after = response.headers.get("Retry-After")
            retry_after_valid = False
            if isinstance(retry_after, str) and len(retry_after) <= 128:
                raw_retry_after = retry_after.strip()
                if raw_retry_after:
                    try:
                        retry_after_valid = int(raw_retry_after) >= 0
                    except ValueError:
                        try:
                            parsed_retry_after = parsedate_to_datetime(
                                raw_retry_after
                            )
                            retry_after_valid = (
                                parsed_retry_after.tzinfo is not None
                            )
                        except (TypeError, ValueError, OverflowError):
                            pass

            rate_limited = (
                remaining_exhausted
                or retry_after_valid
                or "rate limit" in message.casefold()
            )

        if rate_limited:
            raise ReservationError(
                "provider_rate_limited", message, detail=detail
            )
        raise ReservationError("provider_error", message, detail=detail)

    def ref_sha(self, branch: str) -> str | None:
        encoded = quote(f"heads/{branch}", safe="/")
        response = self.request(
            "GET", f"/git/ref/{encoded}", expected={200, 404}
        )
        if response.status_code == 404:
            return None
        payload = response.json()
        sha = (
            payload.get("object", {}).get("sha")
            if isinstance(payload, dict)
            else None
        )
        if type(sha) is not str or _SHA40_RE.fullmatch(sha) is None:
            raise ReservationError(
                "invalid_provider_response",
                "GitHub ref response lacks a commit SHA",
            )
        return sha

    def commit_tree_sha(self, commit_sha: str) -> str:
        response = self.request(
            "GET", f"/git/commits/{commit_sha}", expected={200}
        )
        payload = response.json()
        sha = (
            payload.get("tree", {}).get("sha")
            if isinstance(payload, dict)
            else None
        )
        if type(sha) is not str or _SHA40_RE.fullmatch(sha) is None:
            raise ReservationError(
                "invalid_provider_response",
                "GitHub commit response lacks a tree SHA",
            )
        return sha

    def read_state(
        self, branch: str, *, ref: str | None = None
    ) -> tuple[dict[str, Any], str]:
        response = self.request(
            "GET",
            f"/contents/{STATE_PATH}",
            expected={200, 404},
            params={"ref": ref or branch},
        )
        if response.status_code == 404:
            raise ReservationError(
                "reservation_initializing",
                "reservation ref exists without state",
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ReservationError(
                "invalid_provider_response",
                "GitHub content response is malformed",
            )
        blob_sha = payload.get("sha")
        encoded = payload.get("content")
        encoding = payload.get("encoding")
        if (
            type(blob_sha) is not str
            or _SHA40_RE.fullmatch(blob_sha) is None
            or encoding != "base64"
            or type(encoded) is not str
        ):
            raise ReservationError(
                "invalid_provider_response",
                "GitHub content response is malformed",
            )
        try:
            raw = base64.b64decode(encoded, validate=False)
            state = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReservationError(
                "invalid_state",
                "reservation state is not valid UTF-8 JSON",
            ) from exc
        return _validate_state(state, branch), blob_sha

    def build_commit(
        self,
        parent_sha: str,
        state: dict[str, Any],
        message: str,
    ) -> str:
        tree_sha = self.commit_tree_sha(parent_sha)
        blob = self.request(
            "POST",
            "/git/blobs",
            expected={201},
            json_body={
                "content": _canonical_json(state) + "\n",
                "encoding": "utf-8",
            },
        ).json()
        blob_sha = blob.get("sha") if isinstance(blob, dict) else None
        if type(blob_sha) is not str or _SHA40_RE.fullmatch(blob_sha) is None:
            raise ReservationError(
                "invalid_provider_response",
                "GitHub blob response lacks SHA",
            )
        tree = self.request(
            "POST",
            "/git/trees",
            expected={201},
            json_body={
                "base_tree": tree_sha,
                "tree": [
                    {
                        "path": STATE_PATH,
                        "mode": "100644",
                        "type": "blob",
                        "sha": blob_sha,
                    }
                ],
            },
        ).json()
        new_tree = tree.get("sha") if isinstance(tree, dict) else None
        if type(new_tree) is not str or _SHA40_RE.fullmatch(new_tree) is None:
            raise ReservationError(
                "invalid_provider_response",
                "GitHub tree response lacks SHA",
            )
        commit = self.request(
            "POST",
            "/git/commits",
            expected={201},
            json_body={
                "message": message,
                "tree": new_tree,
                "parents": [parent_sha],
            },
        ).json()
        commit_sha = (
            commit.get("sha") if isinstance(commit, dict) else None
        )
        if (
            type(commit_sha) is not str
            or _SHA40_RE.fullmatch(commit_sha) is None
        ):
            raise ReservationError(
                "invalid_provider_response",
                "GitHub commit response lacks SHA",
            )
        return commit_sha

    def create_ref(self, branch: str, sha: str) -> bool:
        response = self.request(
            "POST",
            "/git/refs",
            expected={201, 422},
            json_body={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        return response.status_code == 201

    def fast_forward_ref(self, branch: str, sha: str) -> bool:
        encoded = quote(f"heads/{branch}", safe="/")
        response = self.request(
            "PATCH",
            f"/git/refs/{encoded}",
            expected={200, 422},
            json_body={"sha": sha, "force": False},
        )
        return response.status_code == 200


def _validate_state(value: Any, branch: str) -> dict[str, Any]:
    required = {
        "schema",
        "work_key",
        "work_key_sha256",
        "branch",
        "status",
        "owner",
        "event_id",
        "generation",
        "reserved_at",
        "updated_at",
        "lease_seconds",
        "lease_expires_at",
        "artifact",
    }
    if type(value) is not dict or set(value) != required:
        raise ReservationError(
            "invalid_state", "reservation state has unexpected fields"
        )
    if value["schema"] != SCHEMA:
        raise ReservationError(
            "invalid_state", "reservation schema mismatch"
        )
    work_key = _work_key(value["work_key"])
    digest = _digest(work_key)
    if (
        value["work_key_sha256"] != digest
        or value["branch"] != branch
        or branch != _branch(work_key)
    ):
        raise ReservationError(
            "invalid_state", "reservation work identity mismatch"
        )
    if value["status"] not in {"ACTIVE", "RELEASED"}:
        raise ReservationError(
            "invalid_state", "reservation status is invalid"
        )
    _owner(value["owner"])
    _event_id(value["event_id"])
    if type(value["generation"]) is not int or value["generation"] <= 0:
        raise ReservationError(
            "invalid_state", "reservation generation is invalid"
        )
    reserved_at = _parse_time(value["reserved_at"], "reserved_at")
    updated_at = _parse_time(value["updated_at"], "updated_at")
    expires_at = _parse_time(
        value["lease_expires_at"], "lease_expires_at"
    )
    lease = _lease_seconds(value["lease_seconds"])
    if updated_at < reserved_at or expires_at < updated_at:
        raise ReservationError(
            "invalid_state", "reservation timestamps are inconsistent"
        )
    if value["status"] == "ACTIVE":
        expected = updated_at + timedelta(seconds=lease)
        if expires_at != expected:
            raise ReservationError(
                "invalid_state",
                "active reservation lease expiry is inconsistent",
            )
    _artifact(value["artifact"])
    return dict(value)


def _new_state(
    *,
    work_key: str,
    branch: str,
    owner: str,
    event_id: str,
    generation: int,
    lease_seconds: int,
    now: datetime,
    artifact: str | None,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "work_key": work_key,
        "work_key_sha256": _digest(work_key),
        "branch": branch,
        "status": "ACTIVE",
        "owner": owner,
        "event_id": event_id,
        "generation": generation,
        "reserved_at": _time(now),
        "updated_at": _time(now),
        "lease_seconds": lease_seconds,
        "lease_expires_at": _time(
            now + timedelta(seconds=lease_seconds)
        ),
        "artifact": artifact,
    }


def _public_state(
    state: dict[str, Any],
    *,
    disposition: str,
    commit_sha: str | None = None,
) -> dict[str, Any]:
    result = {
        "schema": SCHEMA,
        "disposition": disposition,
        "work_key": state["work_key"],
        "work_key_sha256": state["work_key_sha256"],
        "branch": state["branch"],
        "status": state["status"],
        "owner": state["owner"],
        "event_id": state["event_id"],
        "generation": state["generation"],
        "reserved_at": state["reserved_at"],
        "updated_at": state["updated_at"],
        "lease_seconds": state["lease_seconds"],
        "lease_expires_at": state["lease_expires_at"],
        "artifact": state["artifact"],
    }
    if commit_sha is not None:
        result["commit_sha"] = commit_sha
    return result


def reserve(
    github: GitHub,
    *,
    work_key: str,
    owner: str,
    event_id: str,
    lease_seconds: int,
    artifact: str | None,
    base_branch: str,
) -> tuple[dict[str, Any], int]:
    work_key = _work_key(work_key)
    owner = _owner(owner)
    event_id = _event_id(event_id)
    lease_seconds = _lease_seconds(lease_seconds)
    artifact = _artifact(artifact)
    branch = _branch(work_key)
    now = _now()
    head = github.ref_sha(branch)

    if head is None:
        base_sha = github.ref_sha(base_branch)
        if base_sha is None:
            raise ReservationError(
                "base_ref_missing",
                f"base branch does not exist: {base_branch}",
            )
        state = _new_state(
            work_key=work_key,
            branch=branch,
            owner=owner,
            event_id=event_id,
            generation=1,
            lease_seconds=lease_seconds,
            now=now,
            artifact=artifact,
        )
        commit = github.build_commit(
            base_sha,
            state,
            f"custody: reserve {state['work_key_sha256'][:12]}",
        )
        if github.create_ref(branch, commit):
            return (
                _public_state(
                    state,
                    disposition="ACQUIRED",
                    commit_sha=commit,
                ),
                0,
            )
        head = github.ref_sha(branch)
        if head is None:
            raise ReservationError(
                "reservation_race",
                "reservation ref race did not settle",
            )

    current, _blob_sha = github.read_state(branch, ref=head)
    expires = _parse_time(
        current["lease_expires_at"], "lease_expires_at"
    )
    if current["status"] == "ACTIVE" and expires > now:
        disposition = (
            "OWNED" if current["owner"] == owner else "BUSY"
        )
        return (
            _public_state(
                current,
                disposition=disposition,
                commit_sha=head,
            ),
            0 if disposition == "OWNED" else 3,
        )

    state = _new_state(
        work_key=work_key,
        branch=branch,
        owner=owner,
        event_id=event_id,
        generation=current["generation"] + 1,
        lease_seconds=lease_seconds,
        now=now,
        artifact=artifact,
    )
    commit = github.build_commit(
        head,
        state,
        (
            f"custody: reserve {state['work_key_sha256'][:12]} "
            f"gen {state['generation']}"
        ),
    )
    if github.fast_forward_ref(branch, commit):
        return (
            _public_state(
                state,
                disposition="ACQUIRED",
                commit_sha=commit,
            ),
            0,
        )
    winner_head = github.ref_sha(branch)
    if winner_head is None:
        raise ReservationError(
            "reservation_race",
            "reservation ref disappeared during takeover",
        )
    winner, _ = github.read_state(branch, ref=winner_head)
    return (
        _public_state(
            winner,
            disposition="BUSY",
            commit_sha=winner_head,
        ),
        3,
    )


def renew(
    github: GitHub,
    *,
    work_key: str,
    owner: str,
    event_id: str,
    lease_seconds: int,
    artifact: str | None,
) -> tuple[dict[str, Any], int]:
    work_key = _work_key(work_key)
    owner = _owner(owner)
    event_id = _event_id(event_id)
    lease_seconds = _lease_seconds(lease_seconds)
    artifact = _artifact(artifact)
    branch = _branch(work_key)
    head = github.ref_sha(branch)
    if head is None:
        raise ReservationError(
            "not_found", "reservation does not exist"
        )
    current, _ = github.read_state(branch, ref=head)
    if current["status"] != "ACTIVE" or current["owner"] != owner:
        return (
            _public_state(
                current,
                disposition="NOT_OWNER",
                commit_sha=head,
            ),
            3,
        )
    now = _now()
    state = dict(current)
    state.update(
        {
            "event_id": event_id,
            "updated_at": _time(now),
            "lease_seconds": lease_seconds,
            "lease_expires_at": _time(
                now + timedelta(seconds=lease_seconds)
            ),
            "artifact": (
                artifact
                if artifact is not None
                else current["artifact"]
            ),
        }
    )
    commit = github.build_commit(
        head,
        state,
        (
            f"custody: renew {state['work_key_sha256'][:12]} "
            f"gen {state['generation']}"
        ),
    )
    if github.fast_forward_ref(branch, commit):
        return (
            _public_state(
                state,
                disposition="RENEWED",
                commit_sha=commit,
            ),
            0,
        )
    winner_head = github.ref_sha(branch)
    if winner_head is None:
        raise ReservationError(
            "reservation_race",
            "reservation ref disappeared during renew",
        )
    winner, _ = github.read_state(branch, ref=winner_head)
    return (
        _public_state(
            winner,
            disposition="LOST_RACE",
            commit_sha=winner_head,
        ),
        3,
    )


def release(
    github: GitHub,
    *,
    work_key: str,
    owner: str,
    event_id: str,
    artifact: str | None,
) -> tuple[dict[str, Any], int]:
    work_key = _work_key(work_key)
    owner = _owner(owner)
    event_id = _event_id(event_id)
    artifact = _artifact(artifact)
    branch = _branch(work_key)
    head = github.ref_sha(branch)
    if head is None:
        raise ReservationError(
            "not_found", "reservation does not exist"
        )
    current, _ = github.read_state(branch, ref=head)
    if current["status"] != "ACTIVE" or current["owner"] != owner:
        return (
            _public_state(
                current,
                disposition="NOT_OWNER",
                commit_sha=head,
            ),
            3,
        )
    now = _now()
    state = dict(current)
    state.update(
        {
            "status": "RELEASED",
            "event_id": event_id,
            "updated_at": _time(now),
            "lease_expires_at": _time(now),
            "artifact": (
                artifact
                if artifact is not None
                else current["artifact"]
            ),
        }
    )
    commit = github.build_commit(
        head,
        state,
        (
            f"custody: release {state['work_key_sha256'][:12]} "
            f"gen {state['generation']}"
        ),
    )
    if github.fast_forward_ref(branch, commit):
        return (
            _public_state(
                state,
                disposition="RELEASED",
                commit_sha=commit,
            ),
            0,
        )
    winner_head = github.ref_sha(branch)
    if winner_head is None:
        raise ReservationError(
            "reservation_race",
            "reservation ref disappeared during release",
        )
    winner, _ = github.read_state(branch, ref=winner_head)
    return (
        _public_state(
            winner,
            disposition="LOST_RACE",
            commit_sha=winner_head,
        ),
        3,
    )


def status(
    github: GitHub,
    *,
    work_key: str,
) -> tuple[dict[str, Any], int]:
    work_key = _work_key(work_key)
    branch = _branch(work_key)
    head = github.ref_sha(branch)
    if head is None:
        return (
            {
                "schema": SCHEMA,
                "disposition": "OPEN",
                "work_key": work_key,
                "work_key_sha256": _digest(work_key),
                "branch": branch,
            },
            0,
        )
    current, _ = github.read_state(branch, ref=head)
    now = _now()
    expires = _parse_time(
        current["lease_expires_at"], "lease_expires_at"
    )
    disposition = current["status"]
    if current["status"] == "ACTIVE" and expires <= now:
        disposition = "EXPIRED"
    return (
        _public_state(
            current,
            disposition=disposition,
            commit_sha=head,
        ),
        0,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Atomically reserve one swarm work key through a GitHub ref."
        )
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get(REPO_ENV, DEFAULT_REPO),
        help=(
            f"custody repository "
            f"(default: ${REPO_ENV} or {DEFAULT_REPO})"
        ),
    )
    parser.add_argument(
        "--base",
        default=os.environ.get(BASE_ENV, DEFAULT_BASE),
        help=(
            f"base branch for first reservation "
            f"(default: ${BASE_ENV} or {DEFAULT_BASE})"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status_p = sub.add_parser("status")
    status_p.add_argument("work_key")

    for name in ("reserve", "renew"):
        p = sub.add_parser(name)
        p.add_argument("work_key")
        p.add_argument("--owner", required=True)
        p.add_argument("--event-id", required=True)
        p.add_argument(
            "--lease-seconds",
            type=int,
            default=DEFAULT_LEASE_SECONDS,
        )
        p.add_argument("--artifact")

    release_p = sub.add_parser("release")
    release_p.add_argument("work_key")
    release_p.add_argument("--owner", required=True)
    release_p.add_argument("--event-id", required=True)
    release_p.add_argument("--artifact")

    return parser


def _error_json(exc: ReservationError) -> str:
    payload: dict[str, Any] = {
        "error": {
            "code": exc.code,
            "message": exc.message,
        }
    }
    if exc.detail:
        payload["error"]["detail"] = exc.detail
    return _canonical_json(payload)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        github = GitHub(
            args.repo,
            os.environ.get(TOKEN_ENV, ""),
        )
        if args.command == "status":
            result, code = status(
                github,
                work_key=args.work_key,
            )
        elif args.command == "reserve":
            result, code = reserve(
                github,
                work_key=args.work_key,
                owner=args.owner,
                event_id=args.event_id,
                lease_seconds=args.lease_seconds,
                artifact=args.artifact,
                base_branch=args.base,
            )
        elif args.command == "renew":
            result, code = renew(
                github,
                work_key=args.work_key,
                owner=args.owner,
                event_id=args.event_id,
                lease_seconds=args.lease_seconds,
                artifact=args.artifact,
            )
        else:
            result, code = release(
                github,
                work_key=args.work_key,
                owner=args.owner,
                event_id=args.event_id,
                artifact=args.artifact,
            )
        print(_canonical_json(result))
        return code
    except ReservationError as exc:
        print(_error_json(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
