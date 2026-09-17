"""Synthetic, provider-free rehearsal of the settlement trust-registry adapter."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from concierge.reward_settlement_certifier import canonical, verify_registry
from concierge.settlement_trust_registry_adapter import (
    ADAPTER_RECEIPT_SCHEMA,
    BUILD_SCHEMA,
    compile_registry,
    verify_outputs,
)


def main() -> int:
    registry_key = secrets.token_bytes(32)
    adapter_key = secrets.token_bytes(32)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        raw = b'{"synthetic_provider_event":"transfer-confirmed"}\n'
        (root / "provider.bin").write_bytes(raw)
        receipt_body = {
            "schema": ADAPTER_RECEIPT_SCHEMA,
            "adapter_id": "provider-export-v1",
            "capture_id": "synthetic-capture-1",
            "source_family": "PROVIDER",
            "source_id": "synthetic-provider-source-1",
            "source_ref": "provider://synthetic/demo/1",
            "observed_at": "2026-09-17T02:00:00Z",
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "source_size": len(raw),
        }
        adapter_receipt = {
            **receipt_body,
            "signature_hmac_sha256": hmac.new(adapter_key, canonical(receipt_body), hashlib.sha256).hexdigest(),
        }
        (root / "provider.receipt.json").write_bytes(canonical(adapter_receipt))
        manifest = {
            "schema": BUILD_SCHEMA,
            "generated_at": "2026-09-17T03:00:00Z",
            "captures": [{"receipt_path": "provider.receipt.json", "source_path": "provider.bin"}],
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_bytes(canonical(manifest))
        resolver = lambda adapter_id: adapter_key if adapter_id == "provider-export-v1" else b""
        registry, signing_receipt = compile_registry(manifest_path, root, registry_key, resolver)
        verify_registry(registry, registry_key)
        if not verify_outputs(manifest_path, root, registry_key, resolver, registry, signing_receipt):
            raise RuntimeError("synthetic verification failed")
        print("REGISTRY_READY", signing_receipt["receipt_sha256"], len(registry["sources"]))
        print("AUTHORITY", json.dumps(signing_receipt["authority"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
