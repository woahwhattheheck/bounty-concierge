# SPDX-License-Identifier: MIT
"""Admit one GitHub publish/claim operation from shared rail state before provider I/O."""
from __future__ import annotations

from pathlib import Path
import hashlib
import math
import re
from secrets import token_hex
from typing import Any, Callable, Optional, TypeVar, Union

from concierge.github_breaker_ledger import (
    BreakerStateError,
    GitHubBreakerLedger,
    SCOPE_DENIED,
)
from concierge.github_cooldown import CooldownStateError, GitHubCooldown
from concierge.github_rail_availability import availability_snapshot


_HEAD_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
T = TypeVar("T")
_RESOURCE_SCOPE_TTL_SECONDS = 15.0 * 60.0
_INTEGRATION_SCOPE_DENIAL_MARKERS = (
    "resource not accessible by integration",
    "insufficient permissions for integration",
)


def _label(value: str, name: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or "\0" in value
    ):
        raise ValueError(f"{name} must be a non-empty label up to {maximum} bytes")
    return value


def _head(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("expected_head must be a 40-character lowercase Git SHA")
    value = value.strip().lower()
    if _HEAD_SHA.fullmatch(value) is None:
        raise ValueError("expected_head must be a 40-character lowercase Git SHA")
    return value


def _repo(value: str) -> str:
    value = _label(value, "repo", 256).strip()
    if _REPOSITORY.fullmatch(value) is None or any(
        part in {".", ".."} for part in value.split("/")
    ):
        raise ValueError("repo must be owner/name")
    return value


def _resource_breaker_route(rail: str, repo: str, action: str) -> str:
    """Hash one exact publish destination into a bounded breaker route label."""
    payload = (
        b"github-publish-resource-breaker-v1\0"
        + rail.encode("utf-8")
        + b"\0"
        + repo.casefold().encode("utf-8")
        + b"\0"
        + action.encode("utf-8")
    )
    return "publish-resource:" + hashlib.sha256(payload).hexdigest()


def _integration_scope_denied(exc: Exception) -> bool:
    folded = " ".join(str(exc).casefold().split())
    return any(marker in folded for marker in _INTEGRATION_SCOPE_DENIAL_MARKERS)


def _deferred(
    snapshot: dict[str, Any],
    *,
    operation: str,
    action: str,
    repo: str,
    carrier: str,
    expected_head: str,
) -> dict[str, Any]:
    return {
        "status": "RAIL_DEFERRED",
        "provider_called": False,
        "retry_after": snapshot["retry_after_seconds"],
        "operation": operation,
        "action": action,
        "repo": repo,
        "carrier": carrier,
        "expected_head": expected_head,
    }


def execute_publish_operation(
    path: Union[str, Path],
    token: Optional[str],
    *,
    rail: str,
    actor: str,
    operation: str,
    action: str,
    repo: str,
    carrier: str,
    expected_head: str,
    transport: Callable[[], T],
    cooldown_scope: Optional[str] = None,
    recovery_owner: Optional[str] = None,
    scope_breaker_path: Optional[Union[str, Path]] = None,
) -> Union[T, dict[str, Any]]:
    """Execute one GitHub provider operation only when shared rail state admits it.

    HOT and RECOVERY_PROBE_IN_FLIGHT return an exact RAIL_DEFERRED handoff and
    never call transport. RECOVERY_READY must win the existing credential-global
    recovery lease before transport is allowed; followers re-read and defer.
    AVAILABLE proceeds directly.

    Normal provider payloads are returned by identity, unchanged. Provider
    exceptions propagate unchanged and deliberately do not complete the recovery
    lease, leaving its bounded expiry in place. On a normal recovery return, the
    existing CAS cleanup clears only the stale cooldown protected by the lease
    and preserves any newer provider deadline recorded during transport.
    """
    rail = _label(rail, "rail", 128)
    actor = _label(actor, "actor", 128)
    operation = _label(operation, "operation", 160)
    action = _label(action, "action", 128)
    repo = _repo(repo)
    carrier = _label(carrier, "carrier", 1024)
    expected_head = _head(expected_head)
    if recovery_owner is not None:
        recovery_owner = _label(recovery_owner, "recovery_owner", 128)
    if not callable(transport):
        raise ValueError("transport must be callable")

    resource_breaker = None
    if scope_breaker_path is not None:
        resource_breaker = GitHubBreakerLedger(
            scope_breaker_path,
            provider_route=_resource_breaker_route(rail, repo, action),
            credential=token,
            stale_ttl_seconds=_RESOURCE_SCOPE_TTL_SECONDS,
        )
        resource_breaker.cleanup_stale()
        resource_status = resource_breaker.status_snapshot()
        write_status = resource_status.get("operations", {}).get("write")
        if isinstance(write_status, dict) and write_status.get("state") == SCOPE_DENIED:
            age = float(write_status.get("observation_age_seconds", 0.0))
            retry_after = max(1, math.ceil(_RESOURCE_SCOPE_TTL_SECONDS - age))
            result = _deferred(
                {"retry_after_seconds": retry_after},
                operation=operation,
                action=action,
                repo=repo,
                carrier=carrier,
                expected_head=expected_head,
            )
            return result

    snapshot = availability_snapshot(
        path,
        token,
        rail=rail,
        actor=actor,
        cooldown_scope=cooldown_scope,
    )
    availability = snapshot.get("availability")
    if availability in {"HOT", "RECOVERY_PROBE_IN_FLIGHT"}:
        return _deferred(
            snapshot,
            operation=operation,
            action=action,
            repo=repo,
            carrier=carrier,
            expected_head=expected_head,
        )
    if availability not in {"AVAILABLE", "RECOVERY_READY"}:
        raise CooldownStateError("shared GitHub rail availability invalid")

    cooldown = None
    owner = None
    recovery_held = False
    if availability == "RECOVERY_READY":
        cooldown = GitHubCooldown(path, token, cooldown_scope=cooldown_scope)
        owner = recovery_owner or token_hex(16)
        required, acquired, _lease_until = cooldown.claim_recovery_probe(owner)
        if required and not acquired:
            follower = availability_snapshot(
                path,
                token,
                rail=rail,
                actor=actor,
                cooldown_scope=cooldown_scope,
            )
            return _deferred(
                follower,
                operation=operation,
                action=action,
                repo=repo,
                carrier=carrier,
                expected_head=expected_head,
            )
        if not required:
            refreshed = availability_snapshot(
                path,
                token,
                rail=rail,
                actor=actor,
                cooldown_scope=cooldown_scope,
            )
            if refreshed.get("availability") != "AVAILABLE":
                return _deferred(
                    refreshed,
                    operation=operation,
                    action=action,
                    repo=repo,
                    carrier=carrier,
                    expected_head=expected_head,
                )
        recovery_held = required and acquired

    try:
        result = transport()
    except Exception as exc:
        if resource_breaker is not None and _integration_scope_denied(exc):
            try:
                resource_breaker.record_receipt("write", "SCOPE_DENIED")
            except BreakerStateError:
                # Preserve the original provider failure if local coordination
                # state becomes unavailable after the provider attempt.
                pass
        raise
    if recovery_held:
        assert cooldown is not None and owner is not None
        cooldown.complete_recovery_probe(owner)
    return result
