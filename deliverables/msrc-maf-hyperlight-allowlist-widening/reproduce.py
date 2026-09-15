#!/usr/bin/env python3
"""Offline source-model regression for Agent Framework Hyperlight allowed-domain scope."""

from __future__ import annotations
from urllib.parse import urlparse

SOURCE_AGENT_FRAMEWORK_SHA = "61d6c2c23db5d435eaa8c70fa8e5d8770b70720d"
SOURCE_HYPERLIGHT_SANDBOX_060_SHA = "74318c79179faf2eeca6929d01b276cd77e96fe3"


def agent_framework_normalize_domain(target: str) -> str:
    candidate = target.strip()
    if not candidate:
        raise ValueError("Allowed domain entries must not be empty.")
    parsed = urlparse(candidate if "://" in candidate else f"//{candidate}")
    normalized = (parsed.netloc or parsed.path).strip().rstrip("/")
    if not normalized:
        raise ValueError(f"Could not normalize allowed domain entry: {target!r}.")
    return normalized.lower()


def agent_framework_registration_targets(target: str, expand_missing_scheme: bool) -> tuple[str, ...]:
    if not expand_missing_scheme or "://" in target:
        return (target,)
    return (f"http://{target}", f"https://{target}")


def hyperlight_060_allows(permission: str, request: str) -> bool:
    """Model the host/scheme/port/path checks in hyperlight-sandbox 0.6.0 network.rs."""
    permission_url = urlparse(permission)
    request_url = urlparse(request)
    if permission_url.hostname != request_url.hostname:
        return False
    if permission_url.scheme != request_url.scheme:
        return False
    if permission_url.port != request_url.port:
        return False

    permission_path = permission_url.path.rstrip("/")
    request_path = request_url.path
    if permission_path:
        if not request_path.startswith(permission_path):
            return False
        suffix = request_path[len(permission_path) :]
        if suffix and not suffix.startswith("/"):
            return False
    return True


def current_wrapper_permissions(configured: str) -> tuple[str, ...]:
    normalized = agent_framework_normalize_domain(configured)
    # Hyperlight 0.6.0 requires a URL; the wrapper retries a schemeless target
    # by expanding it to both HTTP and HTTPS.
    return agent_framework_registration_targets(normalized, expand_missing_scheme=True)


def main() -> None:
    configured = "https://api.example.com/v1"
    registered = current_wrapper_permissions(configured)
    assert registered == ("http://api.example.com", "https://api.example.com")

    intended_child = "https://api.example.com/v1/items"
    wrong_path = "https://api.example.com/admin"
    downgraded_scheme = "http://api.example.com/v1/items"

    assert hyperlight_060_allows(configured, intended_child)
    assert not hyperlight_060_allows(configured, wrong_path)
    assert not hyperlight_060_allows(configured, downgraded_scheme)

    assert any(hyperlight_060_allows(permission, wrong_path) for permission in registered)
    assert any(hyperlight_060_allows(permission, downgraded_scheme) for permission in registered)

    print("REPRODUCED: explicit URL capability is broadened by wrapper normalization")
    print(f"configured={configured}")
    print(f"registered={registered}")
    print(f"unexpectedly_allowed={wrong_path}")
    print(f"unexpectedly_allowed={downgraded_scheme}")


if __name__ == "__main__":
    main()
