# SPDX-License-Identifier: MIT
"""Fail-closed publication-route planning for bounty work.

The compiler consumes already-observed GitHub/provider/tool evidence and emits a
stable advisory receipt. It never mutates GitHub, applies to a provider, assigns
work, submits a pull request, or proves that an external user account lacks
permission merely because a connector cannot inspect that repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit


class PublicationRouteInputError(ValueError):
    """Raised when a publication-route observation is malformed or contradictory."""


_SCHEMA = "publication-route-gate/v1"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_BRANCH_RE = re.compile(r"^(?!/)(?!.*//)(?!.*\.\.)(?!.*@\{)(?!.*[~^:?*\[\\])[!-~]+(?<![/.])$")
_PR_PATH_RE = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)/?$")
_MAX_URL_CHARS = 2048
_ALLOWED_ACCESS = frozenset({"write", "read", "resource_not_accessible", "unknown"})
_ALLOWED_ASSIGNMENT = frozenset({"actor", "none", "other", "unknown"})
_ALLOWED_PRIMITIVES = frozenset(
    {
        "create_branch",
        "create_file",
        "update_file",
        "create_blob",
        "create_tree",
        "create_commit",
        "update_ref",
        "create_pull_request",
        "merge_pull_request",
    }
)
_MIN_BRANCH_PR = frozenset({"create_branch", "create_pull_request"})
_CONTENTS_WRITE = frozenset({"create_file", "update_file"})
_GIT_OBJECT_WRITE = frozenset({"create_blob", "create_tree", "create_commit", "update_ref"})
_READY = frozenset({"DIRECT_BRANCH_PR", "OWNED_FORK_PR", "REUSE_EXISTING_PR"})


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _object(value: Any, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise PublicationRouteInputError(f"{field} must be an object")
    return value


def _string(value: Any, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise PublicationRouteInputError(f"{field} must be a non-empty trimmed string")
    return value


def _repo(value: Any, field: str) -> str:
    repo = _string(value, field)
    if _REPO_RE.fullmatch(repo) is None:
        raise PublicationRouteInputError(f"{field} must be owner/repository")
    return repo


def _login(value: Any, field: str) -> str:
    login = _string(value, field)
    if _LOGIN_RE.fullmatch(login) is None:
        raise PublicationRouteInputError(f"{field} must be a GitHub-style login")
    return login.casefold()


def _branch(value: Any, field: str) -> str:
    branch = _string(value, field)
    if len(branch) > 255 or _BRANCH_RE.fullmatch(branch) is None:
        raise PublicationRouteInputError(f"{field} must be a safe Git ref name")
    return branch


def _timestamp(value: Any, field: str) -> datetime:
    raw = _string(value, field)
    if not raw.endswith("Z"):
        raise PublicationRouteInputError(f"{field} must be UTC RFC3339 ending in Z")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise PublicationRouteInputError(f"{field} must be valid RFC3339") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise PublicationRouteInputError(f"{field} must be UTC")
    return parsed


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicationRouteInputError(f"{field} must be a non-negative integer")
    return value


def _optional_bool(value: Any, field: str) -> Optional[bool]:
    if value is None:
        return None
    if type(value) is not bool:
        raise PublicationRouteInputError(f"{field} must be boolean or null")
    return value


def _canonical_pr_url(value: Any, upstream_repo: str) -> Optional[str]:
    if value is None:
        return None
    source = _string(value, "existing_pr_url")
    if len(source) > _MAX_URL_CHARS or any(ch.isspace() for ch in source):
        raise PublicationRouteInputError("existing_pr_url must be a compact canonical URL")
    if "\\" in source or "%" in source or "?" in source or "#" in source:
        raise PublicationRouteInputError("existing_pr_url must not contain aliases, query, or fragment")
    try:
        parsed = urlsplit(source)
        port = parsed.port
    except ValueError as exc:
        raise PublicationRouteInputError("existing_pr_url must be valid") from exc
    if parsed.scheme.casefold() != "https" or (parsed.hostname or "").casefold() not in {
        "github.com",
        "www.github.com",
    }:
        raise PublicationRouteInputError("existing_pr_url must use canonical https GitHub")
    if parsed.username is not None or parsed.password is not None or port is not None:
        raise PublicationRouteInputError("existing_pr_url must not contain userinfo or port")
    match = _PR_PATH_RE.fullmatch(parsed.path)
    if match is None:
        raise PublicationRouteInputError("existing_pr_url must identify a GitHub pull request")
    owner, repo, _ = match.groups()
    if f"{owner}/{repo}".casefold() != upstream_repo.casefold():
        raise PublicationRouteInputError("existing_pr_url must belong to upstream_repo")
    return source


def _primitives(value: Any) -> list[str]:
    if type(value) is not list:
        raise PublicationRouteInputError("publication_primitives must be a list")
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        primitive = _string(item, f"publication_primitives[{index}]")
        if primitive not in _ALLOWED_PRIMITIVES:
            raise PublicationRouteInputError(
                f"publication_primitives[{index}] is not an allowed primitive"
            )
        if primitive in seen:
            raise PublicationRouteInputError("publication_primitives must not contain duplicates")
        seen.add(primitive)
        result.append(primitive)
    return sorted(result)


def _has_content_write_path(primitives: frozenset[str]) -> bool:
    """Return whether observed primitives can publish tested bytes to a branch."""
    return bool(_CONTENTS_WRITE & primitives) or _GIT_OBJECT_WRITE <= primitives


def compile_publication_route(request: dict[str, Any]) -> dict[str, Any]:
    """Compile one publication observation into a deterministic advisory receipt."""
    request = _object(request, "request")
    if request.get("schema") != _SCHEMA:
        raise PublicationRouteInputError(f"schema must equal {_SCHEMA}")

    actor = _login(request.get("actor_login"), "actor_login")
    upstream_repo = _repo(request.get("upstream_repo"), "upstream_repo")
    upstream_default = _branch(
        request.get("upstream_default_branch"), "upstream_default_branch"
    )
    target_base = _branch(request.get("target_base_branch"), "target_base_branch")

    integration_access = _string(
        request.get("integration_access"), "integration_access"
    ).casefold()
    if integration_access not in _ALLOWED_ACCESS:
        raise PublicationRouteInputError(
            "integration_access must be write, read, resource_not_accessible, or unknown"
        )

    installed_fork_raw = request.get("installed_fork_repo")
    installed_fork = (
        None if installed_fork_raw is None else _repo(installed_fork_raw, "installed_fork_repo")
    )
    fork_push_access = _optional_bool(
        request.get("installed_fork_push_access"), "installed_fork_push_access"
    )
    if installed_fork is None and fork_push_access is not None:
        raise PublicationRouteInputError(
            "installed_fork_push_access must be null without installed_fork_repo"
        )
    if installed_fork is not None and fork_push_access is None:
        raise PublicationRouteInputError(
            "installed_fork_push_access must be observed when installed_fork_repo is set"
        )
    if installed_fork is not None and installed_fork.casefold() == upstream_repo.casefold():
        raise PublicationRouteInputError("installed_fork_repo must differ from upstream_repo")

    primitives = _primitives(request.get("publication_primitives"))
    primitive_set = frozenset(primitives)
    existing_pr = _canonical_pr_url(request.get("existing_pr_url"), upstream_repo)
    existing_pr_state_raw = request.get("existing_pr_state")
    if existing_pr is None:
        if existing_pr_state_raw is not None:
            raise PublicationRouteInputError(
                "existing_pr_state must be null without existing_pr_url"
            )
        existing_pr_state = None
    else:
        existing_pr_state = _string(existing_pr_state_raw, "existing_pr_state").casefold()
        if existing_pr_state not in {"open", "closed", "merged", "unknown"}:
            raise PublicationRouteInputError(
                "existing_pr_state must be open, closed, merged, or unknown"
            )

    fork_actor_owned = (
        None
        if installed_fork is None
        else installed_fork.split("/", 1)[0].casefold() == actor
    )

    assignment_required = request.get("provider_requires_assignment")
    if type(assignment_required) is not bool:
        raise PublicationRouteInputError("provider_requires_assignment must be boolean")
    assignment = _string(request.get("provider_assignment"), "provider_assignment").casefold()
    if assignment not in _ALLOWED_ASSIGNMENT:
        raise PublicationRouteInputError(
            "provider_assignment must be actor, none, other, or unknown"
        )
    actor_applied = _optional_bool(request.get("actor_applied"), "actor_applied")

    observed = _timestamp(request.get("observed_at"), "observed_at")
    evaluated = _timestamp(request.get("evaluated_at"), "evaluated_at")
    if evaluated < observed:
        raise PublicationRouteInputError("evaluated_at must not precede observed_at")
    age_seconds = int((evaluated - observed).total_seconds())
    max_age = _nonnegative_int(
        request.get("max_snapshot_age_seconds", 900), "max_snapshot_age_seconds"
    )
    if max_age < 1 or max_age > 86400:
        raise PublicationRouteInputError(
            "max_snapshot_age_seconds must be between 1 and 86400"
        )

    reasons: list[str] = []
    if age_seconds > max_age:
        disposition = "HOLD_STALE_OBSERVATION"
        next_action = "REFRESH_REPOSITORY_PROVIDER_AND_TOOL_EVIDENCE"
        reasons.append("OBSERVATION_STALE")
    elif existing_pr is not None and existing_pr_state == "unknown":
        disposition = "HOLD_EXISTING_PR_STATE_UNKNOWN"
        next_action = "REFRESH_EXISTING_PR_STATE"
        reasons.append("EXISTING_PR_STATE_UNKNOWN")
    elif existing_pr is not None:
        disposition = "HOLD_EXISTING_PR_NOT_OPEN"
        next_action = "REFRESH_ISSUE_AND_PUBLICATION_ROUTE_BEFORE_NEW_WORK"
        reasons.append("EXISTING_PR_NOT_OPEN")
    elif assignment == "other":
        disposition = "HOLD_ASSIGNED_TO_OTHER"
        next_action = "DO_NOT_START_PARALLEL_IMPLEMENTATION"
        reasons.append("ASSIGNED_TO_OTHER")
    elif assignment == "unknown":
        disposition = "HOLD_ASSIGNMENT_UNKNOWN"
        next_action = "REFRESH_PROVIDER_ASSIGNMENT_STATE"
        reasons.append("ASSIGNMENT_STATE_UNKNOWN")
    elif assignment_required and assignment != "actor":
        if actor_applied is True:
            disposition = "WAIT_ASSIGNMENT"
            next_action = "WAIT_FOR_PROVIDER_ASSIGNMENT"
            reasons.append("ASSIGNMENT_REQUIRED_APPLICATION_ALREADY_SENT")
        elif actor_applied is False:
            disposition = "APPLICATION_ONLY"
            next_action = "USE_PROVIDER_APPLICATION_ROUTE_BEFORE_IMPLEMENTATION"
            reasons.append("ASSIGNMENT_REQUIRED_BEFORE_IMPLEMENTATION")
        else:
            disposition = "HOLD_APPLICATION_UNKNOWN"
            next_action = "REFRESH_PROVIDER_APPLICATION_STATE"
            reasons.append("APPLICATION_STATE_UNKNOWN")
    elif existing_pr is not None and existing_pr_state == "open":
        disposition = "REUSE_EXISTING_PR"
        next_action = "REFRESH_AND_CONTINUE_EXISTING_PR"
        reasons.append("EXISTING_OPEN_UPSTREAM_PR_PRESENT")
    elif (
        integration_access == "write"
        and _MIN_BRANCH_PR <= primitive_set
        and _has_content_write_path(primitive_set)
    ):
        disposition = "DIRECT_BRANCH_PR"
        next_action = "PUBLISH_BRANCH_AND_PR_TO_BOUND_BASE"
        reasons.append("UPSTREAM_WRITE_OBSERVED")
    elif (
        installed_fork is not None
        and fork_actor_owned is True
        and fork_push_access is True
        and _MIN_BRANCH_PR <= primitive_set
        and _has_content_write_path(primitive_set)
    ):
        disposition = "OWNED_FORK_PR"
        next_action = "PUBLISH_TO_INSTALLED_FORK_THEN_OPEN_UPSTREAM_PR"
        reasons.append("INSTALLED_FORK_PUSH_OBSERVED")
        if integration_access == "resource_not_accessible":
            reasons.append("UPSTREAM_CONNECTOR_ACCESS_UNKNOWN_NOT_PERMISSION_DENIAL")
    else:
        disposition = "HANDOFF_REQUIRED"
        next_action = "HAND_OFF_TESTED_BYTES_OR_ESTABLISH_PUBLICATION_PATH"
        if integration_access == "resource_not_accessible":
            reasons.append("UPSTREAM_CONNECTOR_ACCESS_UNKNOWN_NOT_PERMISSION_DENIAL")
        elif integration_access == "read":
            reasons.append("UPSTREAM_WRITE_NOT_OBSERVED")
        elif integration_access == "unknown":
            reasons.append("UPSTREAM_ACCESS_UNKNOWN")
        else:
            reasons.append("PUBLICATION_PRIMITIVES_INSUFFICIENT")
        if installed_fork is None:
            reasons.append("NO_INSTALLED_FORK_OBSERVED")
        elif fork_actor_owned is not True:
            reasons.append("INSTALLED_FORK_NOT_ACTOR_OWNED")
        elif fork_push_access is not True:
            reasons.append("INSTALLED_FORK_PUSH_NOT_OBSERVED")
        if not _MIN_BRANCH_PR <= primitive_set or not _has_content_write_path(primitive_set):
            reasons.append("PUBLICATION_PRIMITIVES_INSUFFICIENT")
        reasons = list(dict.fromkeys(reasons))

    body = {
        "schema": _SCHEMA,
        "disposition": disposition,
        "advisory_next_action": next_action,
        "reason_codes": reasons,
        "target": {
            "actor_login": actor,
            "upstream_repo": upstream_repo,
            "upstream_default_branch": upstream_default,
            "target_base_branch": target_base,
            "target_is_default_branch": target_base == upstream_default,
            "existing_pr_url": existing_pr,
            "existing_pr_state": existing_pr_state,
        },
        "observed_publication_path": {
            "integration_access": integration_access,
            "installed_fork_repo": installed_fork,
            "installed_fork_actor_owned": fork_actor_owned,
            "installed_fork_push_access": fork_push_access,
            "publication_primitives": primitives,
        },
        "provider_gate": {
            "requires_assignment": assignment_required,
            "assignment": assignment,
            "actor_applied": actor_applied,
        },
        "freshness": {
            "observed_at": request["observed_at"],
            "evaluated_at": request["evaluated_at"],
            "snapshot_age_seconds": age_seconds,
            "max_snapshot_age_seconds": max_age,
        },
        "authority": {
            "advisory_only": True,
            "repository_write_authority": False,
            "provider_application_authority": False,
            "assignment_authority": False,
            "pull_request_submission_authority": False,
            "merge_authority": False,
            "payment_or_wallet_authority": False,
        },
        "interpretation": {
            "resource_not_accessible_means": "CONNECTOR_ACCESS_UNKNOWN",
            "resource_not_accessible_does_not_prove": "USER_LACKS_REPOSITORY_PERMISSION",
        },
    }
    return {**body, "receipt_sha256": _sha256_json(body)}


def verify_receipt(receipt: dict[str, Any]) -> bool:
    """Verify deterministic receipt integrity and the fixed advisory authority ceiling."""
    if type(receipt) is not dict:
        return False
    digest = receipt.get("receipt_sha256")
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return False
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    if body.get("authority") != {
        "advisory_only": True,
        "repository_write_authority": False,
        "provider_application_authority": False,
        "assignment_authority": False,
        "pull_request_submission_authority": False,
        "merge_authority": False,
        "payment_or_wallet_authority": False,
    }:
        return False
    if body.get("interpretation") != {
        "resource_not_accessible_means": "CONNECTOR_ACCESS_UNKNOWN",
        "resource_not_accessible_does_not_prove": "USER_LACKS_REPOSITORY_PERMISSION",
    }:
        return False
    return _sha256_json(body) == digest


def format_summary(receipt: dict[str, Any]) -> str:
    target = receipt["target"]
    reasons = ",".join(receipt["reason_codes"]) or "none"
    return (
        f"disposition={receipt['disposition']} "
        f"repo={target['upstream_repo']} base={target['target_base_branch']} "
        f"next={receipt['advisory_next_action']} reasons={reasons} "
        f"receipt_sha256={receipt['receipt_sha256']}"
    )


def _load(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        payload = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    return _object(payload, "request")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.publication_route_gate",
        description=(
            "Compile a deterministic advisory publication route from already-observed "
            "GitHub/provider/tool evidence. This command performs no network mutation."
        ),
    )
    parser.add_argument("observation", help="observation JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true", help="emit the complete receipt")
    args = parser.parse_args(argv)
    try:
        receipt = compile_publication_route(_load(args.observation))
    except (OSError, json.JSONDecodeError, PublicationRouteInputError) as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, indent=2, sort_keys=True) if args.json else format_summary(receipt))
    return 0 if receipt["disposition"] in _READY else 2


if __name__ == "__main__":
    raise SystemExit(main())
