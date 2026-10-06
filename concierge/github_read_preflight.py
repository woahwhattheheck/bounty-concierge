# SPDX-License-Identifier: MIT
"""Admit GitHub read/discovery operations before provider I/O."""
from __future__ import annotations

import math
from pathlib import Path
import re
from secrets import token_hex
from typing import Any, Callable, Optional, TypeVar, Union

from concierge.github_cooldown import (
    CooldownStateError,
    GitHubCooldown,
    cooldown_deadline,
)
from concierge.github_rail_availability import availability_snapshot


T = TypeVar("T")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_ALLOWED_OPERATION_CLASSES = {
    "search",
    "comments",
    "branch-head",
    "issue-pr-read",
}
_RATE_ERRORS = {"PRIMARY_RATE_LIMIT", "SECONDARY_RATE_LIMIT"}
_NON_RATE_ERRORS = {"PERMISSION_DENIED", "PRE_PROVIDER_SAFETY_BLOCK"}
_ALLOWED_ERRORS = _RATE_ERRORS | _NON_RATE_ERRORS


def _label(value: str, name: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or "\0" in value
    ):
        raise ValueError(f"{name} must be a non-empty label up to {maximum} bytes")
    return value


def _repo(value: str) -> str:
    value = _label(value, "repo", 256).strip()
    if _REPOSITORY.fullmatch(value) is None or any(
        part in {".", ".."} for part in value.split("/")
    ):
        raise ValueError("repo must be owner/name")
    return value


class GitHubReadFailure(RuntimeError):
    """Normalized read/discovery failure supplied by a provider adapter.

    Only actual provider rate-limit evidence may alter shared cooldown state.
    Permission failures and pre-provider platform safety blocks are represented
    explicitly so callers can bank or reroute work without poisoning the rate
    ledger.
    """

    def __init__(
        self,
        error_class: str,
        *,
        retry_after_seconds: int | None = None,
        retry_at: str | None = None,
        reset_at: int | None = None,
    ) -> None:
        if error_class not in _ALLOWED_ERRORS:
            raise ValueError("unsupported GitHub read failure class")
        if retry_after_seconds is not None and (
            type(retry_after_seconds) is not int or retry_after_seconds < 0
        ):
            raise ValueError("retry_after_seconds must be a non-negative integer")
        if retry_at is not None:
            _label(retry_at, "retry_at", 128)
        if reset_at is not None and (type(reset_at) is not int or reset_at < 0):
            raise ValueError("reset_at must be a non-negative integer")
        super().__init__(error_class)
        self.error_class = error_class
        self.retry_after_seconds = retry_after_seconds
        self.retry_at = retry_at
        self.reset_at = reset_at
        self.provider_called = error_class != "PRE_PROVIDER_SAFETY_BLOCK"


def _metadata(
    *,
    rail: str,
    actor: str,
    operation: str,
    operation_class: str,
    repo: str,
    target: str,
) -> dict[str, Any]:
    return {
        "rail": rail,
        "actor": actor,
        "operation": operation,
        "operation_class": operation_class,
        "repo": repo,
        "target": target,
    }


def _deferred(
    snapshot: dict[str, Any],
    *,
    metadata: dict[str, Any],
    provider_called: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "status": "READ_DEFERRED",
        "provider_called": provider_called,
        "retry_after": snapshot["retry_after_seconds"],
        "reason": reason,
        **metadata,
    }


def _failed(
    *,
    metadata: dict[str, Any],
    failure: GitHubReadFailure,
) -> dict[str, Any]:
    return {
        "status": "READ_FAILED",
        "provider_called": failure.provider_called,
        "retry_after": 0,
        "reason": failure.error_class,
        **metadata,
    }


def execute_read_operation(
    path: Union[str, Path],
    token: Optional[str],
    *,
    rail: str,
    actor: str,
    operation: str,
    operation_class: str,
    repo: str,
    target: str,
    transport: Callable[[], T],
    cooldown_scope: Optional[str] = None,
    recovery_owner: Optional[str] = None,
) -> Union[T, dict[str, Any]]:
    """Execute one GitHub read/discovery operation if shared rail state admits it.

    HOT and RECOVERY_PROBE_IN_FLIGHT return READ_DEFERRED without provider I/O.
    RECOVERY_READY permits exactly one credential-global recovery probe; peers
    defer until its bounded lease expires or the probe completes. AVAILABLE
    calls the supplied transport directly.

    Provider adapters may raise GitHubReadFailure with normalized rate-limit
    hints. PRIMARY_RATE_LIMIT and SECONDARY_RATE_LIMIT extend the existing
    GitHubCooldown state and return a replayable READ_DEFERRED receipt.
    PERMISSION_DENIED and PRE_PROVIDER_SAFETY_BLOCK return READ_FAILED without
    changing rate state. Unexpected exceptions propagate unchanged.

    READ_DEFERRED intentionally carries operation metadata so dispatch can bank
    or replay the work, or select a cache/public mirror/legitimately healthy
    actor, without rediscovery. This helper never rotates credentials.
    """
    rail = _label(rail, "rail", 128)
    actor = _label(actor, "actor", 128)
    operation = _label(operation, "operation", 160)
    operation_class = _label(operation_class, "operation_class", 64)
    if operation_class not in _ALLOWED_OPERATION_CLASSES:
        raise ValueError("unsupported GitHub read operation class")
    repo = _repo(repo)
    target = _label(target, "target", 1024)
    if recovery_owner is not None:
        recovery_owner = _label(recovery_owner, "recovery_owner", 128)
    if not callable(transport):
        raise ValueError("transport must be callable")

    metadata = _metadata(
        rail=rail,
        actor=actor,
        operation=operation,
        operation_class=operation_class,
        repo=repo,
        target=target,
    )
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
            metadata=metadata,
            provider_called=False,
            reason=str(availability),
        )
    if availability not in {"AVAILABLE", "RECOVERY_READY"}:
        raise CooldownStateError("shared GitHub rail availability invalid")

    cooldown = GitHubCooldown(path, token, cooldown_scope=cooldown_scope)
    owner = recovery_owner or token_hex(16)
    recovery_held = False

    if availability == "RECOVERY_READY":
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
                metadata=metadata,
                provider_called=False,
                reason="RECOVERY_PROBE_IN_FLIGHT",
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
                    metadata=metadata,
                    provider_called=False,
                    reason=str(refreshed.get("availability")),
                )
        recovery_held = required and acquired

    try:
        result = transport()
    except GitHubReadFailure as failure:
        if failure.error_class not in _RATE_ERRORS:
            # Do not complete or extend a recovery lease from permission or
            # pre-provider safety evidence. Its existing short lease bounds the
            # failed probe while the underlying rate ledger remains untouched.
            return _failed(metadata=metadata, failure=failure)

        unknown_secondary = (
            failure.error_class == "SECONDARY_RATE_LIMIT"
            and failure.retry_after_seconds is None
            and failure.retry_at is None
        )
        if unknown_secondary:
            deadline = cooldown.extend_unknown_secondary()
            if recovery_held:
                cooldown.defer_recovery_probe(owner, deadline)
                recovery_held = False
        else:
            deadline = cooldown_deadline(
                retry_seconds=failure.retry_after_seconds,
                retry_at=failure.retry_at,
                reset_at=failure.reset_at,
                primary_exhausted=failure.error_class == "PRIMARY_RATE_LIMIT",
            )
            if recovery_held:
                cooldown.defer_recovery_probe(owner, deadline)
                recovery_held = False
            else:
                cooldown.extend(deadline)

        deferred_snapshot = availability_snapshot(
            path,
            token,
            rail=rail,
            actor=actor,
            cooldown_scope=cooldown_scope,
        )
        return _deferred(
            deferred_snapshot,
            metadata=metadata,
            provider_called=True,
            reason=failure.error_class,
        )

    if recovery_held:
        cooldown.complete_recovery_probe(owner)
    return result
