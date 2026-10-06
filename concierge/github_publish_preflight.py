# SPDX-License-Identifier: MIT
"""Admit one GitHub publish/claim operation from shared rail state before provider I/O."""
from __future__ import annotations

from pathlib import Path
import re
from secrets import token_hex
from typing import Any, Callable, Optional, TypeVar, Union

from concierge.github_cooldown import CooldownStateError, GitHubCooldown
from concierge.github_rail_availability import availability_snapshot


_SCHEMA = "github-publish-preflight/v1"
_HEAD_SHA = re.compile(r"[0-9a-f]{40}\Z")
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


def _deferred(
    snapshot: dict[str, Any],
    *,
    operation_id: str,
    carrier: str,
    expected_head: str,
) -> dict[str, Any]:
    return {
        "schema": _SCHEMA,
        "decision": "RAIL_DEFERRED",
        "handoff_reason": "RAIL_DEFERRED",
        "rail": snapshot["rail"],
        "actor": snapshot["actor"],
        "availability": snapshot["availability"],
        "retry_after_seconds": snapshot["retry_after_seconds"],
        "operation_id": operation_id,
        "carrier": carrier,
        "expected_head": expected_head,
    }


def run_github_provider_operation(
    path: Union[str, Path],
    token: Optional[str],
    *,
    rail: str,
    actor: str,
    operation_id: str,
    carrier: str,
    expected_head: str,
    provider_call: Callable[[], T],
    cooldown_scope: Optional[str] = None,
    recovery_owner: Optional[str] = None,
) -> Union[T, dict[str, Any]]:
    """Run one provider operation only when the selected GitHub rail is admitted.

    Known-hot rails return a machine-readable RAIL_DEFERRED handoff without
    touching the provider. A credential-global RECOVERY_READY state must first
    win the existing atomic recovery lease; followers defer while the winner
    owns the lease. AVAILABLE rails proceed directly.

    The provider result is returned unchanged. Provider exceptions likewise
    propagate unchanged; a recovery lease then expires naturally rather than
    guessing a new provider deadline. On a successful recovery call, existing
    CAS cleanup clears only the stale cooldown protected by the lease and
    preserves any newer deadline recorded during the call.
    """
    operation_id = _label(operation_id, "operation_id", 160)
    carrier = _label(carrier, "carrier", 1024)
    expected_head = _head(expected_head)
    if recovery_owner is not None:
        recovery_owner = _label(recovery_owner, "recovery_owner", 128)
    if not callable(provider_call):
        raise ValueError("provider_call must be callable")

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
            operation_id=operation_id,
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
                operation_id=operation_id,
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
                    operation_id=operation_id,
                    carrier=carrier,
                    expected_head=expected_head,
                )
        recovery_held = required and acquired

    result = provider_call()
    if recovery_held:
        assert cooldown is not None and owner is not None
        cooldown.complete_recovery_probe(owner)
    return result
