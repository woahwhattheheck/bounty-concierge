# SPDX-License-Identifier: MIT
"""Admit one GitHub publish/claim operation from shared rail state before provider I/O."""
from __future__ import annotations

from pathlib import Path
import re
from secrets import token_hex
from typing import Any, Callable, Optional, TypeVar, Union

from concierge.github_cooldown import CooldownStateError, GitHubCooldown
from concierge.github_rail_availability import availability_snapshot


_HEAD_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
T = TypeVar("T")


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
    operation = _label(operation, "operation", 160)
    action = _label(action, "action", 128)
    repo = _repo(repo)
    carrier = _label(carrier, "carrier", 1024)
    expected_head = _head(expected_head)
    if recovery_owner is not None:
        recovery_owner = _label(recovery_owner, "recovery_owner", 128)
    if not callable(transport):
        raise ValueError("transport must be callable")

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

    result = transport()
    if recovery_held:
        assert cooldown is not None and owner is not None
        cooldown.complete_recovery_probe(owner)
    return result
