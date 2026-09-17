# SPDX-License-Identifier: MIT
"""Trusted-adapter producer for reward settlement source registries.

The consumer certifier intentionally accepts only a signed out-of-band registry.
This module is the controlled producer: it verifies adapter-authenticated capture
receipts, re-hashes retained source bytes, derives authority from a code-owned
adapter allowlist, and signs exactly the registry schema consumed by
``reward_settlement_certifier``.  Settlement JSON is never an input here.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from .reward_settlement_certifier import REGISTRY_SCHEMA, canonical, verify_registry

ADAPTER_RECEIPT_SCHEMA = "bounty-concierge/reward-settlement-adapter-receipt/v1"
BUILD_SCHEMA = "bounty-concierge/reward-settlement-registry-build/v1"
RECEIPT_SCHEMA = "bounty-concierge/reward-settlement-registry-signing-receipt/v1"
MAX_JSON_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 16 * 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+\-]{0,239}$")
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class TrustRegistryAdapterError(ValueError):
    pass


@dataclass(frozen=True)
class FamilyPolicy:
    authority: str
    source_ref_prefix: str
    max_age_s: int


@dataclass(frozen=True)
class AdapterPolicy:
    key_env: str
    families: Mapping[str, FamilyPolicy]


# Trust is intentionally code-owned. A caller may select an adapter ID but may
# not redefine which authority a source family represents. These adapters are
# receipt protocols; the actual connector/provider capture process is separate.
ADAPTER_POLICIES: Mapping[str, AdapterPolicy] = {
    "repository-export-v1": AdapterPolicy(
        key_env="REWARD_SETTLEMENT_REPOSITORY_ADAPTER_KEY",
        families={"REPOSITORY": FamilyPolicy("REPOSITORY", "repo://", 30 * 86400)},
    ),
    "official-offer-export-v1": AdapterPolicy(
        key_env="REWARD_SETTLEMENT_OFFER_ADAPTER_KEY",
        families={"OFFICIAL_OFFER": FamilyPolicy("OFFICIAL_OFFER", "offer://", 30 * 86400)},
    ),
    "sponsor-export-v1": AdapterPolicy(
        key_env="REWARD_SETTLEMENT_SPONSOR_ADAPTER_KEY",
        families={"SPONSOR": FamilyPolicy("SPONSOR", "sponsor://", 14 * 86400)},
    ),
    "provider-export-v1": AdapterPolicy(
        key_env="REWARD_SETTLEMENT_PROVIDER_ADAPTER_KEY",
        families={"PROVIDER": FamilyPolicy("PROVIDER", "provider://", 7 * 86400)},
    ),
    "wallet-export-v1": AdapterPolicy(
        key_env="REWARD_SETTLEMENT_WALLET_ADAPTER_KEY",
        families={"WALLET": FamilyPolicy("WALLET", "wallet://", 2 * 86400)},
    ),
    "bank-export-v1": AdapterPolicy(
        key_env="REWARD_SETTLEMENT_BANK_ADAPTER_KEY",
        families={"BANK": FamilyPolicy("BANK", "bank://", 2 * 86400)},
    ),
    "operator-capture-v1": AdapterPolicy(
        key_env="REWARD_SETTLEMENT_OPERATOR_CAPTURE_KEY",
        families={"OPERATOR_CAPTURE": FamilyPolicy("OPERATOR_CAPTURE", "capture://", 7 * 86400)},
    ),
}


def _pairs(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise TrustRegistryAdapterError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def loads_strict(raw: bytes) -> Any:
    if type(raw) is not bytes or len(raw) > MAX_JSON_BYTES or raw.startswith(b"\xef\xbb\xbf"):
        raise TrustRegistryAdapterError("JSON input must be bounded BOM-free bytes")
    try:
        text = raw.decode("utf-8", "strict")
        def reject_float(value: str) -> None:
            raise TrustRegistryAdapterError(f"floating JSON number forbidden: {value}")
        def reject_constant(value: str) -> None:
            raise TrustRegistryAdapterError(f"non-finite JSON number forbidden: {value}")
        return json.loads(text, object_pairs_hook=_pairs, parse_float=reject_float, parse_constant=reject_constant)
    except TrustRegistryAdapterError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrustRegistryAdapterError("invalid strict UTF-8 JSON") from exc


def _exact(value: Any, keys: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise TrustRegistryAdapterError(f"{name} must have exact keys {sorted(keys)}")
    return value


def _token(value: Any, name: str) -> str:
    if type(value) is not str or TOKEN.fullmatch(value) is None:
        raise TrustRegistryAdapterError(f"{name} invalid")
    return value


def _timestamp(value: Any, name: str) -> tuple[str, datetime]:
    if type(value) is not str or TS.fullmatch(value) is None:
        raise TrustRegistryAdapterError(f"{name} must be canonical UTC seconds")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise TrustRegistryAdapterError(f"{name} is not a real UTC timestamp") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise TrustRegistryAdapterError(f"{name} must be canonical UTC seconds")
    return value, parsed


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_json(value: Any) -> str:
    return _sha_bytes(canonical(value))


def _stable_read(path: Path, max_bytes: int) -> bytes:
    try:
        pre = path.lstat()
    except OSError as exc:
        raise TrustRegistryAdapterError(f"cannot stat input {path}") from exc
    if stat.S_ISLNK(pre.st_mode) or not stat.S_ISREG(pre.st_mode) or pre.st_size > max_bytes:
        raise TrustRegistryAdapterError(f"input must be a bounded regular non-symlink file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise TrustRegistryAdapterError(f"cannot open input {path}") from exc
    try:
        fst_pre = os.fstat(fd)
        if not stat.S_ISREG(fst_pre.st_mode) or fst_pre.st_size > max_bytes:
            raise TrustRegistryAdapterError(f"input changed before read: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise TrustRegistryAdapterError(f"input too large: {path}")
        fst_post = os.fstat(fd)
    finally:
        os.close(fd)
    try:
        post = path.lstat()
    except OSError as exc:
        raise TrustRegistryAdapterError(f"input disappeared during read: {path}") from exc
    identity_pre = (pre.st_dev, pre.st_ino, pre.st_size, getattr(pre, "st_mtime_ns", int(pre.st_mtime * 1e9)))
    identity_fd_pre = (fst_pre.st_dev, fst_pre.st_ino, fst_pre.st_size, getattr(fst_pre, "st_mtime_ns", int(fst_pre.st_mtime * 1e9)))
    identity_fd_post = (fst_post.st_dev, fst_post.st_ino, fst_post.st_size, getattr(fst_post, "st_mtime_ns", int(fst_post.st_mtime * 1e9)))
    identity_post = (post.st_dev, post.st_ino, post.st_size, getattr(post, "st_mtime_ns", int(post.st_mtime * 1e9)))
    if not (identity_pre == identity_fd_pre == identity_fd_post == identity_post):
        raise TrustRegistryAdapterError(f"input changed during read: {path}")
    raw = b"".join(chunks)
    if len(raw) != pre.st_size:
        raise TrustRegistryAdapterError(f"partial input read: {path}")
    return raw


def _resolve_relative(root: Path, value: Any, name: str) -> Path:
    if type(value) is not str or not value or "\x00" in value:
        raise TrustRegistryAdapterError(f"{name} must be a non-empty relative path")
    rel = Path(value)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise TrustRegistryAdapterError(f"{name} must stay beneath the capture root")
    root_real = root.resolve(strict=True)
    candidate = root_real.joinpath(rel)
    try:
        parent_real = candidate.parent.resolve(strict=True)
    except OSError as exc:
        raise TrustRegistryAdapterError(f"{name} parent missing") from exc
    try:
        parent_real.relative_to(root_real)
    except ValueError as exc:
        raise TrustRegistryAdapterError(f"{name} escapes capture root") from exc
    return candidate


def _adapter_receipt_body(receipt: dict[str, Any]) -> dict[str, Any]:
    return {key: receipt[key] for key in (
        "schema", "adapter_id", "capture_id", "source_family", "source_id",
        "source_ref", "observed_at", "source_sha256", "source_size",
    )}


def verify_adapter_receipt(receipt: Any, raw: bytes, key: bytes, generated_at: str) -> dict[str, str]:
    receipt = _exact(receipt, {
        "schema", "adapter_id", "capture_id", "source_family", "source_id",
        "source_ref", "observed_at", "source_sha256", "source_size",
        "signature_hmac_sha256",
    }, "adapter receipt")
    if receipt["schema"] != ADAPTER_RECEIPT_SCHEMA:
        raise TrustRegistryAdapterError("adapter receipt schema invalid")
    adapter_id = _token(receipt["adapter_id"], "adapter_id")
    policy = ADAPTER_POLICIES.get(adapter_id)
    if policy is None:
        raise TrustRegistryAdapterError("adapter_id is not code-authorized")
    family = _token(receipt["source_family"], "source_family")
    family_policy = policy.families.get(family)
    if family_policy is None:
        raise TrustRegistryAdapterError("source_family is not allowed for adapter_id")
    _token(receipt["capture_id"], "capture_id")
    _token(receipt["source_id"], "source_id")
    source_ref = _token(receipt["source_ref"], "source_ref")
    if not source_ref.startswith(family_policy.source_ref_prefix):
        raise TrustRegistryAdapterError("source_ref is outside the adapter allowlist")
    observed_text, observed_dt = _timestamp(receipt["observed_at"], "observed_at")
    _, generated_dt = _timestamp(generated_at, "generated_at")
    if observed_dt > generated_dt:
        raise TrustRegistryAdapterError("adapter evidence is future-dated")
    age_s = int((generated_dt - observed_dt).total_seconds())
    if age_s > family_policy.max_age_s:
        raise TrustRegistryAdapterError("adapter evidence is stale")
    source_sha = receipt["source_sha256"]
    if type(source_sha) is not str or HEX64.fullmatch(source_sha) is None or source_sha != _sha_bytes(raw):
        raise TrustRegistryAdapterError("retained source digest mismatch")
    if type(receipt["source_size"]) is not int or type(receipt["source_size"]) is bool or receipt["source_size"] != len(raw):
        raise TrustRegistryAdapterError("retained source size mismatch")
    signature = receipt["signature_hmac_sha256"]
    if type(key) is not bytes or len(key) < 16:
        raise TrustRegistryAdapterError("adapter verification key must be at least 16 bytes")
    if type(signature) is not str or HEX64.fullmatch(signature) is None:
        raise TrustRegistryAdapterError("adapter receipt signature invalid")
    expected = hmac.new(key, canonical(_adapter_receipt_body(receipt)), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise TrustRegistryAdapterError("adapter receipt HMAC verification failed")
    return {
        "source_id": receipt["source_id"],
        "source_ref": source_ref,
        "source_sha256": source_sha,
        "observed_at": observed_text,
        "authority": family_policy.authority,
    }


def _read_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _stable_read(path, MAX_JSON_BYTES)
    value = loads_strict(raw)
    value = _exact(value, {"schema", "generated_at", "captures"}, "manifest")
    if value["schema"] != BUILD_SCHEMA or type(value["captures"]) is not list:
        raise TrustRegistryAdapterError("manifest schema/captures invalid")
    _timestamp(value["generated_at"], "manifest.generated_at")
    return value, raw


def compile_registry(
    manifest_path: Path,
    capture_root: Path,
    registry_key: bytes,
    adapter_key_resolver: Callable[[str], bytes],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(registry_key) is not bytes or len(registry_key) < 16:
        raise TrustRegistryAdapterError("registry signing key must be at least 16 bytes")
    manifest, manifest_raw = _read_manifest(manifest_path)
    sources: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_fingerprints: set[tuple[str, str, str, str]] = set()
    capture_bindings: list[dict[str, Any]] = []
    for index, item in enumerate(manifest["captures"]):
        item = _exact(item, {"receipt_path", "source_path"}, f"captures[{index}]")
        receipt_path = _resolve_relative(capture_root, item["receipt_path"], f"captures[{index}].receipt_path")
        source_path = _resolve_relative(capture_root, item["source_path"], f"captures[{index}].source_path")
        receipt_raw = _stable_read(receipt_path, MAX_JSON_BYTES)
        receipt = loads_strict(receipt_raw)
        if type(receipt) is not dict or type(receipt.get("adapter_id")) is not str:
            raise TrustRegistryAdapterError(f"captures[{index}] adapter receipt missing adapter_id")
        adapter_id = receipt["adapter_id"]
        if adapter_id not in ADAPTER_POLICIES:
            raise TrustRegistryAdapterError(f"captures[{index}] adapter_id is not code-authorized")
        adapter_key = adapter_key_resolver(adapter_id)
        source_raw = _stable_read(source_path, MAX_SOURCE_BYTES)
        source = verify_adapter_receipt(receipt, source_raw, adapter_key, manifest["generated_at"])
        sid = source["source_id"]
        if sid in seen_ids:
            raise TrustRegistryAdapterError(f"duplicate source_id: {sid}")
        fingerprint = (source["source_ref"], source["source_sha256"], source["observed_at"], source["authority"])
        if fingerprint in seen_fingerprints:
            raise TrustRegistryAdapterError(f"trusted source remint detected for {sid}")
        seen_ids.add(sid)
        seen_fingerprints.add(fingerprint)
        sources.append(source)
        capture_bindings.append({
            "adapter_id": adapter_id,
            "capture_id": receipt["capture_id"],
            "adapter_receipt_sha256": _sha_bytes(receipt_raw),
            "source_sha256": source["source_sha256"],
            "source_size": len(source_raw),
        })
    sources.sort(key=lambda row: row["source_id"])
    capture_bindings.sort(key=lambda row: (row["adapter_id"], row["capture_id"]))
    body = {"schema": REGISTRY_SCHEMA, "generated_at": manifest["generated_at"], "sources": sources}
    signature = hmac.new(registry_key, canonical(body), hashlib.sha256).hexdigest()
    registry = {**body, "signature_hmac_sha256": signature}
    try:
        verify_registry(registry, registry_key)
    except Exception as exc:
        raise TrustRegistryAdapterError("built registry is not consumable by reward_settlement_certifier") from exc
    receipt_body = {
        "schema": RECEIPT_SCHEMA,
        "generated_at": manifest["generated_at"],
        "manifest_sha256": _sha_bytes(manifest_raw),
        "registry_body_sha256": _sha_json(body),
        "registry_sha256": _sha_json(registry),
        "source_count": len(sources),
        "capture_bindings": capture_bindings,
        "authority": {
            "send_outbound": False,
            "request_payout": False,
            "mutate_provider": False,
            "mutate_wallet_or_bank": False,
            "recognize_accounting_revenue": False,
        },
    }
    receipt = {**receipt_body, "receipt_sha256": _sha_json(receipt_body)}
    return registry, receipt


def verify_outputs(
    manifest_path: Path,
    capture_root: Path,
    registry_key: bytes,
    adapter_key_resolver: Callable[[str], bytes],
    registry: Any,
    receipt: Any,
) -> bool:
    try:
        rebuilt_registry, rebuilt_receipt = compile_registry(manifest_path, capture_root, registry_key, adapter_key_resolver)
        return hmac.compare_digest(canonical(rebuilt_registry), canonical(registry)) and hmac.compare_digest(canonical(rebuilt_receipt), canonical(receipt))
    except (OSError, TrustRegistryAdapterError, TypeError, ValueError):
        return False


def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags, 0o600)
    except OSError as exc:
        raise TrustRegistryAdapterError("output must not already exist and must not be a symlink") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _key_from_fd(fd: int) -> bytes:
    if type(fd) is not int or fd < 0:
        raise TrustRegistryAdapterError("key fd invalid")
    raw = os.read(fd, 4097)
    if len(raw) > 4096:
        raise TrustRegistryAdapterError("key fd payload too large")
    key = raw.rstrip(b"\r\n")
    if len(key) < 16:
        raise TrustRegistryAdapterError("registry signing key must be at least 16 bytes")
    return key


def _registry_key(ns: argparse.Namespace) -> bytes:
    if ns.registry_key_fd is not None:
        return _key_from_fd(ns.registry_key_fd)
    value = os.environ.get(ns.registry_key_env)
    if not value:
        raise TrustRegistryAdapterError(f"missing registry signing secret: {ns.registry_key_env}")
    return value.encode("utf-8")


def _environment_adapter_key(adapter_id: str) -> bytes:
    policy = ADAPTER_POLICIES.get(adapter_id)
    if policy is None:
        raise TrustRegistryAdapterError("adapter_id is not code-authorized")
    value = os.environ.get(policy.key_env)
    if not value:
        raise TrustRegistryAdapterError(f"missing adapter verification secret for {adapter_id}")
    key = value.encode("utf-8")
    if len(key) < 16:
        raise TrustRegistryAdapterError(f"adapter verification secret too short for {adapter_id}")
    return key


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--capture-root", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--registry-key-env", default="REWARD_SETTLEMENT_TRUST_KEY")
    parser.add_argument("--registry-key-fd", type=int)
    parser.add_argument("--verify", action="store_true")
    ns = parser.parse_args(argv)
    try:
        registry_key = _registry_key(ns)
        if ns.verify:
            registry = loads_strict(_stable_read(ns.registry, MAX_JSON_BYTES))
            receipt = loads_strict(_stable_read(ns.receipt, MAX_JSON_BYTES))
            if not verify_outputs(ns.manifest, ns.capture_root, registry_key, _environment_adapter_key, registry, receipt):
                raise TrustRegistryAdapterError("registry/receipt verification failed")
            print("VERIFIED")
            return 0
        registry, receipt = compile_registry(ns.manifest, ns.capture_root, registry_key, _environment_adapter_key)
        created: list[Path] = []
        try:
            _write_exclusive(ns.registry, canonical(registry) + b"\n")
            created.append(ns.registry)
            _write_exclusive(ns.receipt, canonical(receipt) + b"\n")
            created.append(ns.receipt)
        except Exception:
            for path in reversed(created):
                try:
                    path.unlink()
                except OSError:
                    pass
            raise
        print(receipt["receipt_sha256"])
        return 0
    except (OSError, TrustRegistryAdapterError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
