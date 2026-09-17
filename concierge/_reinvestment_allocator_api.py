# SPDX-License-Identifier: MIT
"""Sealed public API factory for externally signed reinvestment review."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Tuple

from ._reinvestment_allocator_transport import make_worker_invoker


def build_api(error_type: type, module_file: str):
    """Capture the full parent launch graph before caller code receives it."""
    dumps = json.dumps
    loads = json.loads
    json_decode_error = json.JSONDecodeError
    sha256 = hashlib.sha256
    compare_digest = hmac.compare_digest
    copy_deep = deepcopy
    max_json_bytes = 4 * 1024 * 1024
    max_worker_stderr_bytes = 16 * 1024
    max_worker_seconds = 180
    receipt_schema = "realized-reinvestment-review/v4"
    sha256_re = re.compile(r"^[0-9a-f]{64}$")
    protocol = "realized-reinvestment-isolated-worker/v1"
    executable = os.path.realpath(sys.executable)
    worker_path = os.path.realpath(
        str(Path(module_file).with_name("_reinvestment_allocator_worker.py"))
    )
    sealed_environment = dict(os.environ)
    for unsafe_name in tuple(sealed_environment):
        if unsafe_name.startswith("PYTHON") or unsafe_name in {
            "COVERAGE_PROCESS_START",
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
            "DYLD_INSERT_LIBRARIES",
            "DYLD_LIBRARY_PATH",
        }:
            sealed_environment.pop(unsafe_name, None)
    sealed_environment["PYTHONUTF8"] = "1"
    sealed_environment["PYTHONIOENCODING"] = "utf-8"
    sealed_environment_items = tuple(sorted(sealed_environment.items()))
    del sealed_environment

    def canonical_json(value: Any) -> bytes:
        try:
            payload = dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, RuntimeError) as exc:
            raise error_type("value is not canonical JSON") from exc
        if len(payload) > max_json_bytes:
            raise error_type("canonical JSON value is too large")
        return payload

    def duplicate_safe_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise error_type(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    def parse_json(raw: bytes, label: str) -> Any:
        if len(raw) > max_json_bytes:
            raise error_type(f"{label} is too large")
        try:
            return loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=duplicate_safe_object,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    error_type(
                        f"non-finite JSON value {value} is not allowed"
                    )
                ),
            )
        except error_type:
            raise
        except (UnicodeDecodeError, json_decode_error) as exc:
            raise error_type(f"{label} is not strict UTF-8 JSON") from exc

    def snapshot_json(value: Any, label: str) -> Any:
        return parse_json(canonical_json(value), label)

    def validate_wallet(wallet: Any) -> str:
        if (
            type(wallet) is not str
            or not wallet
            or wallet != wallet.strip()
            or len(wallet) > 256
            or any(char.isspace() or not char.isprintable() for char in wallet)
        ):
            raise error_type(
                "wallet must be one bounded printable identifier"
            )
        return wallet

    def scope_sha256(
        closeout_manifest_items: Any,
        payment_bindings: Any,
        effort_log: Any,
        *,
        wallet: str,
    ) -> str:
        scope = {
            "wallet": validate_wallet(wallet),
            "closeout_manifest_items": snapshot_json(
                closeout_manifest_items, "closeout manifest"
            ),
            "payment_bindings": snapshot_json(
                payment_bindings, "payment bindings"
            ),
            "effort_log": snapshot_json(effort_log, "effort log"),
        }
        return sha256(canonical_json(scope)).hexdigest()

    invoke_worker = make_worker_invoker(
        error_type=error_type,
        canonical_json=canonical_json,
        parse_json=parse_json,
        sealed_environment_items=sealed_environment_items,
        executable=executable,
        worker_path=worker_path,
        max_json_bytes=max_json_bytes,
        max_worker_stderr_bytes=max_worker_stderr_bytes,
        max_worker_seconds=max_worker_seconds,
    )

    def compile_review(
        closeout_manifest_items: Any,
        payment_bindings: Any,
        effort_log: Any,
        evidence_authority: Any,
        taxonomy: Any,
        policy: Any,
        capacity_minutes: Any,
        *,
        wallet: str,
        max_closeout_pages: int = 10,
    ) -> Dict[str, Any]:
        request = {
            "protocol": protocol,
            "operation": "compile",
            "closeout_manifest_items": snapshot_json(
                closeout_manifest_items, "closeout manifest"
            ),
            "payment_bindings": snapshot_json(
                payment_bindings, "payment bindings"
            ),
            "effort_log": snapshot_json(effort_log, "effort log"),
            "evidence_authority": snapshot_json(
                evidence_authority, "evidence authority"
            ),
            "taxonomy": snapshot_json(taxonomy, "taxonomy"),
            "policy": snapshot_json(policy, "policy"),
            "capacity_minutes": capacity_minutes,
            "wallet": validate_wallet(wallet),
            "max_closeout_pages": max_closeout_pages,
        }
        response = invoke_worker(request)
        if set(response) != {"protocol", "receipt"}:
            raise error_type(
                "isolated worker compile response has unexpected keys"
            )
        if response["protocol"] != protocol:
            raise error_type("isolated worker protocol mismatch")
        receipt = response["receipt"]
        if type(receipt) is not dict or receipt.get("schema") != receipt_schema:
            raise error_type(
                "isolated worker returned an invalid receipt schema"
            )
        return receipt

    def verify_current(
        receipt: Any,
        closeout_manifest_items: Any,
        payment_bindings: Any,
        effort_log: Any,
        evidence_authority: Any,
        taxonomy: Any,
        policy: Any,
        capacity_minutes: Any,
        *,
        wallet: str,
        max_closeout_pages: int = 10,
    ) -> bool:
        if type(receipt) is not dict:
            return False
        request = {
            "protocol": protocol,
            "operation": "verify-current",
            "receipt": snapshot_json(receipt, "receipt"),
            "closeout_manifest_items": snapshot_json(
                closeout_manifest_items, "closeout manifest"
            ),
            "payment_bindings": snapshot_json(
                payment_bindings, "payment bindings"
            ),
            "effort_log": snapshot_json(effort_log, "effort log"),
            "evidence_authority": snapshot_json(
                evidence_authority, "evidence authority"
            ),
            "taxonomy": snapshot_json(taxonomy, "taxonomy"),
            "policy": snapshot_json(policy, "policy"),
            "capacity_minutes": capacity_minutes,
            "wallet": validate_wallet(wallet),
            "max_closeout_pages": max_closeout_pages,
        }
        try:
            response = invoke_worker(request)
        except error_type:
            return False
        return (
            type(response) is dict
            and set(response) == {"protocol", "valid"}
            and response.get("protocol")
            == protocol
            and type(response.get("valid")) is bool
            and response["valid"]
        )

    def verify_integrity_only(receipt: Any) -> bool:
        if type(receipt) is not dict:
            return False
        digest = receipt.get("receipt_sha256")
        if type(digest) is not str or sha256_re.fullmatch(digest) is None:
            return False
        candidate = copy_deep(receipt)
        candidate.pop("receipt_sha256", None)
        try:
            return compare_digest(sha256(canonical_json(candidate)).hexdigest(), digest)
        except error_type:
            return False

    return compile_review, verify_current, verify_integrity_only, scope_sha256
