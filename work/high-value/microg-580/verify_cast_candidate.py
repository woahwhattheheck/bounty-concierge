#!/usr/bin/env python3
"""Static source oracle for a current-master microG #580 Cast consolidation.

This does not replace Gradle or hardware testing. It rejects candidate trees that are
missing the wire/session/listener invariants already demonstrated by the live carriers.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


FILES = {
    "context": "play-services-cast-framework/core/src/main/java/com/google/android/gms/cast/framework/internal/CastContextImpl.java",
    "router": "play-services-cast-framework/core/src/main/java/com/google/android/gms/cast/framework/internal/MediaRouterCallbackImpl.java",
    "impl": "play-services-cast/core/src/main/java/org/microg/gms/cast/CastDeviceControllerImpl.java",
    "service": "play-services-cast/core/src/main/java/org/microg/gms/cast/CastDeviceControllerService.java",
    "controller_aidl": "play-services-cast/src/main/aidl/com/google/android/gms/cast/internal/ICastDeviceController.aidl",
    "listener_aidl": "play-services-cast/src/main/aidl/com/google/android/gms/cast/internal/ICastDeviceControllerListener.aidl",
}


def read_tree(root: Path) -> tuple[dict[str, str], list[str]]:
    text: dict[str, str] = {}
    failures: list[str] = []
    for key, rel in FILES.items():
        path = root / rel
        try:
            text[key] = path.read_text(encoding="utf-8")
        except OSError as exc:
            failures.append(f"MISSING {rel}: {exc}")
    return text, failures


def require(pattern: str, text: str, label: str, failures: list[str]) -> None:
    if re.search(pattern, text, re.MULTILINE | re.DOTALL) is None:
        failures.append(f"MISSING INVARIANT: {label}")


def method_body(text: str, signature_pattern: str) -> str:
    match = re.search(signature_pattern + r"\s*\{", text, re.MULTILINE)
    if not match:
        return ""
    start = match.end()
    depth = 1
    i = start
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[start : i - 1] if depth == 0 else ""


def check(root: Path) -> tuple[list[str], list[str]]:
    src, failures = read_tree(root)
    warnings: list[str] = []
    if failures:
        return failures, warnings

    require(r"\bvoid\s+connect\s*\(\s*\)\s*=\s*16\s*;", src["controller_aidl"],
            "ICastDeviceController connect transaction 16", failures)
    require(r"\bvoid\s+setListener\s*\(\s*ICastDeviceControllerListener\s+\w+\s*\)\s*=\s*17\s*;",
            src["controller_aidl"], "ICastDeviceController setListener transaction 17", failures)
    require(r"\bvoid\s+unregisterListener\s*\(\s*\)\s*=\s*18\s*;", src["controller_aidl"],
            "ICastDeviceController unregisterListener transaction 18", failures)
    require(r"\bvoid\s+onConnectedWithResult\s*\(\s*int\s+\w+\s*\)\s*=\s*13\s*;",
            src["listener_aidl"], "listener onConnectedWithResult(int) transaction 13", failures)

    require(r"GmsService\.CAST\s*,\s*GmsService\.CAST_API", src["service"],
            "service accepts CAST_API 161 alongside CAST", failures)
    require(r"onPostInitCompleteWithConnectionInfo\s*\(", src["service"],
            "service returns ConnectionInfo", failures)
    require(r"request\.(?:apiFeatures|defaultFeatures)", src["service"],
            "service binds advertised features to request features", failures)

    require(r"registerMediaRouterCallbackImpl\s*\(", src["context"],
            "CastContext registers MediaRouterCallbackImpl", failures)
    require(r"addCallback\s*\(", src["context"],
            "CastContext requests router callback/discovery", failures)
    require(r"startsWith\s*\(\s*defaultCategory\s*\)", src["context"],
            "default provider handles suffixed Cast category", failures)

    require(r"defaultSessionProvider\s*==\s*null", src["router"],
            "route callback fails closed on missing provider", failures)
    require(r"instanceof\s+SessionImpl", src["router"],
            "route callback validates session wrapper", failures)
    require(r"routeInfoExtras\s*!=\s*null\s*\?\s*routeInfoExtras\s*:\s*extras", src["router"],
            "route callback falls back to callback extras", failures)

    require(r"\bvoid\s+connect\s*\(\s*\)", src["impl"],
            "controller implements connectionless connect", failures)
    require(r"\bvoid\s+setListener\s*\(\s*ICastDeviceControllerListener", src["impl"],
            "controller implements out-of-band listener registration", failures)
    require(r"linkToDeath\s*\(", src["impl"],
            "listener binder death is tracked", failures)
    require(r"deadBinder\s*!=\s*this\.listenerBinder", src["impl"],
            "stale binder death is identity-gated", failures)

    disconnect = method_body(src["impl"], r"public\s+void\s+disconnect\s*\(\s*\)")
    if not disconnect:
        failures.append("MISSING INVARIANT: disconnect() body")
    else:
        d = disconnect.find("disconnectChromecast()")
        c = disconnect.find("clearListener()")
        if d < 0 or c < 0 or d > c:
            failures.append("ORDERING: disconnect must tear down Cast before clearing listener")

    raw = method_body(src["impl"], r"public\s+void\s+rawMessageReceived\s*\([^)]*\)")
    if not raw:
        failures.append("MISSING INVARIANT: rawMessageReceived() body")
    elif "onSendMessageSuccess" in raw:
        failures.append("MESSAGE CONTRACT: inbound rawMessageReceived must not signal send success")

    send = method_body(src["impl"], r"public\s+void\s+sendMessage\s*\([^)]*\)")
    if not send:
        failures.append("MISSING INVARIANT: sendMessage() body")
    else:
        s = send.find("sendRawRequest")
        ok = send.find("onSendMessageSuccess")
        if s < 0 or ok < 0 or s > ok:
            failures.append("MESSAGE CONTRACT: outgoing send success must follow sendRawRequest")

    descriptor = root / (
        "play-services-cast-framework/core/src/main/java/com/google/android/gms/dynamite/"
        "descriptors/com/google/android/gms/cast/framework/dynamite/ModuleDescriptor.java"
    )
    if not descriptor.exists():
        warnings.append("SUPPORTING FIX ABSENT: #3554 Cast framework ModuleDescriptor")

    return failures, warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path, help="path to a GmsCore working tree")
    args = parser.parse_args()
    root = args.repo.resolve()
    failures, warnings = check(root)
    for line in warnings:
        print(f"WARN {line}")
    if failures:
        for line in failures:
            print(f"FAIL {line}")
        print(f"RESULT FAIL ({len(failures)} hard invariant failures)")
        return 1
    print("RESULT PASS (source invariants only; Gradle + hardware proof still required)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
