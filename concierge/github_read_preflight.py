# SPDX-License-Identifier: MIT
"""Rate-aware admission for high-fanout GitHub read/discovery operations."""
from __future__ import annotations

from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from secrets import token_hex
from typing import Any, Callable, Generic, Mapping, Optional, TypeVar, Union

from concierge.github_cooldown import CooldownStateError, GitHubCooldown, cooldown_deadline
from concierge.github_rail_availability import availability_snapshot


T = TypeVar("T")


@dataclass(frozen=True)
class GitHubReadResult(Generic[T]):
    """Normalized provider read result used only for rate-limit classification."""

    value: T
    status_code: int = 200
    headers: Mapping[str, str] = field(default_factory=dict)
    message: str | None = None


def _label(value: str, name: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or "\0" in value
    ):
        raise ValueError(f"{name} must be a non-empty label up to {maximum} bytes")
    return value


def _headers(value: Mapping[str, str]) -> dict[str, str]:
    try:
        items = value.items()
    except AttributeError as exc:
        raise ValueError("headers must be a mapping") from exc
    normalized: dict[str, str] = {}
    for key, item in items:
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError("headers must contain string keys and values")
        normalized[key.casefold()] = item
    return normalized


def _integer_header(headers: Mapping[str, str], name: str) -> int | None:
    value = headers.get(name.casefold())
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value.isascii() or not value.isdigit() or len(value) > 12:
        return None
    return int(value)


def _message(result: GitHubReadResult[Any]) -> str:
    if isinstance(result.message, str):
        return result.message
    if isinstance(result.value, dict):
        value = result.value.get("message")
        if isinstance(value, str):
            return value
    return ""


def _retry_at(headers: Mapping[str, str]) -> str | None:
    raw = headers.get("retry-after")
    if not isinstance(raw, str) or len(raw) > 128:
        return None
    if raw.strip().isascii() and raw.strip().isdigit():
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.isoformat()


def _deferred(
    snapshot: dict[str, Any],
    *,
    provider_called: bool,
    reason: str,
    operation: str,
    operation_class: str,
    resource: str,
) -> dict[str, Any]:
    return {
        "status": "READ_DEFERRED",
        "provider_called": provider_called,
        "reason": reason,
        "retry_after": snapshot["retry_after_seconds"],
        "rail": snapshot["rail"],
        "actor": snapshot["actor"],
        "operation": operation,
        "operation_class": operation_class,
        "resource": resource,
    }


def execute_read_operation(
    path: Union[str, Path],
    token: Optional[str],
    *,
    rail: str,
    actor: str,
    operation: str,
    operation_class: str,
    resource: str,
    transport: Callable[[], GitHubReadResult[T]],
    cooldown_scope: Optional[str] = None,
    recovery_owner: Optional[str] = None,
) -> Union[GitHubReadResult[T], dict[str, Any]]:
    """Admit one GitHub read/search operation without stampeding a hot rail.

    HOT and RECOVERY_PROBE_IN_FLIGHT return READ_DEFERRED without transport.
    RECOVERY_READY admits exactly one credential-global recovery probe through
    the existing GitHubCooldown lease; peers defer. AVAILABLE calls transport.

    Only provider rate-limit evidence extends cooldown state. Permission/auth
    failures and pre-provider exceptions do not create rate cooldowns.
    """
    operation = _label(operation, "operation", 160)
    operation_class = _label(operation_class, "operation_class", 96)
    resource = _label(resource, "resource", 1024)
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
            provider_called=False,
            reason=availability,
            operation=operation,
            operation_class=operation_class,
            resource=resource,
        )
    if availability not in {"AVAILABLE", "RECOVERY_READY"}:
        raise CooldownStateError("shared GitHub rail availability invalid")

    cooldown: GitHubCooldown | None = None
    owner: str | None = None
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
                provider_called=False,
                reason="RECOVERY_PROBE_IN_FLIGHT",
                operation=operation,
                operation_class=operation_class,
                resource=resource,
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
                    provider_called=False,
                    reason=str(refreshed.get("availability")),
                    operation=operation,
                    operation_class=operation_class,
                    resource=resource,
                )
        recovery_held = required and acquired

    # Exceptions deliberately propagate. A pre-provider safety/permission block
    # therefore cannot be misclassified as quota evidence. If this was a
    # recovery probe, its short lease expires naturally just like a transport
    # exception in github_publish_preflight.
    result = transport()
    if not isinstance(result, GitHubReadResult):
        raise TypeError("transport must return GitHubReadResult")
    if type(result.status_code) is not int or not 100 <= result.status_code <= 599:
        raise ValueError("status_code must be an integer HTTP status")
    headers = _headers(result.headers)
    remaining = _integer_header(headers, "X-RateLimit-Remaining")
    reset_at = _integer_header(headers, "X-RateLimit-Reset")
    retry_seconds = _integer_header(headers, "Retry-After")
    retry_at = _retry_at(headers)
    message = _message(result).casefold()

    primary_exhausted = remaining == 0 or "api rate limit exceeded" in message
    throttled = (
        result.status_code == 429
        or (
            result.status_code == 403
            and (
                primary_exhausted
                or retry_seconds is not None
                or retry_at is not None
            )
        )
        or "rate limit" in message
    )
    if throttled or primary_exhausted:
        if cooldown is None:
            cooldown = GitHubCooldown(path, token, cooldown_scope=cooldown_scope)

        unknown_secondary = (
            throttled
            and not primary_exhausted
            and retry_seconds is None
            and retry_at is None
        )
        if unknown_secondary:
            deadline = cooldown.extend_unknown_secondary()
            if recovery_held:
                assert owner is not None
                cooldown.defer_recovery_probe(owner, deadline)
                recovery_held = False
            reason = "SECONDARY_RATE_LIMIT"
        else:
            deadline = cooldown_deadline(
                retry_seconds=retry_seconds,
                retry_at=retry_at,
                reset_at=reset_at,
                primary_exhausted=primary_exhausted,
            )
            if recovery_held:
                assert owner is not None
                cooldown.defer_recovery_probe(owner, deadline)
                recovery_held = False
            else:
                cooldown.extend(deadline)
            reason = (
                "PRIMARY_RATE_LIMIT"
                if primary_exhausted
                else "SECONDARY_RATE_LIMIT"
            )

        blocked = availability_snapshot(
            path,
            token,
            rail=rail,
            actor=actor,
            cooldown_scope=cooldown_scope,
        )
        return _deferred(
            blocked,
            provider_called=True,
            reason=reason,
            operation=operation,
            operation_class=operation_class,
            resource=resource,
        )

    if recovery_held:
        assert cooldown is not None and owner is not None
        cooldown.complete_recovery_probe(owner)
    return result
