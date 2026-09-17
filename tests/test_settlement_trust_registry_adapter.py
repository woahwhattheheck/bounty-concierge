from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from concierge.reward_settlement_certifier import canonical, verify_registry
from concierge import settlement_trust_registry_adapter as sut

REGISTRY_KEY = b"registry-test-key-32-bytes-minimum!!"
ADAPTER_KEY = b"adapter-test-key-32-bytes-minimum!!!"
GENERATED = "2026-09-17T03:00:00Z"
OBSERVED = "2026-09-17T02:00:00Z"


def make_adapter_receipt(raw: bytes, *, source_id="source-1", source_ref="provider://case/1", observed_at=OBSERVED,
                         adapter_id="provider-export-v1", family="PROVIDER", capture_id="capture-1", key=ADAPTER_KEY):
    body = {
        "schema": sut.ADAPTER_RECEIPT_SCHEMA,
        "adapter_id": adapter_id,
        "capture_id": capture_id,
        "source_family": family,
        "source_id": source_id,
        "source_ref": source_ref,
        "observed_at": observed_at,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_size": len(raw),
    }
    return {**body, "signature_hmac_sha256": hmac.new(key, canonical(body), hashlib.sha256).hexdigest()}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.raw = b'{"provider_event":"confirmed"}\n'
        (self.root / "source.bin").write_bytes(self.raw)
        self.receipt = make_adapter_receipt(self.raw)
        (self.root / "receipt.json").write_bytes(canonical(self.receipt))
        self.manifest = {
            "schema": sut.BUILD_SCHEMA,
            "generated_at": GENERATED,
            "captures": [{"receipt_path": "receipt.json", "source_path": "source.bin"}],
        }
        self.manifest_path = self.root / "manifest.json"
        self.manifest_path.write_bytes(canonical(self.manifest))

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def resolver(adapter_id):
        if adapter_id != "provider-export-v1":
            raise AssertionError(adapter_id)
        return ADAPTER_KEY

    def compile(self):
        return sut.compile_registry(self.manifest_path, self.root, REGISTRY_KEY, self.resolver)

    def write_receipt(self, receipt):
        (self.root / "receipt.json").write_bytes(canonical(receipt))

    def test_01_happy_path_is_consumed_by_certifier(self):
        registry, receipt = self.compile()
        trusted, body_sha = verify_registry(registry, REGISTRY_KEY)
        self.assertEqual(trusted["source-1"]["authority"], "PROVIDER")
        self.assertEqual(receipt["registry_body_sha256"], body_sha)
        self.assertTrue(sut.verify_outputs(self.manifest_path, self.root, REGISTRY_KEY, self.resolver, registry, receipt))
        self.assertFalse(receipt["authority"]["recognize_accounting_revenue"])

    def test_02_authority_is_code_owned_not_receipt_authored(self):
        bad = dict(self.receipt); bad["authority"] = "BANK"
        self.write_receipt(bad)
        with self.assertRaises(sut.TrustRegistryAdapterError): self.compile()

    def test_03_wrong_adapter_hmac_rejected(self):
        bad = dict(self.receipt); bad["signature_hmac_sha256"] = "0" * 64
        self.write_receipt(bad)
        with self.assertRaisesRegex(sut.TrustRegistryAdapterError, "HMAC"): self.compile()

    def test_04_raw_tamper_rejected(self):
        (self.root / "source.bin").write_bytes(self.raw + b"x")
        with self.assertRaisesRegex(sut.TrustRegistryAdapterError, "digest mismatch"): self.compile()

    def test_05_future_observation_rejected(self):
        self.write_receipt(make_adapter_receipt(self.raw, observed_at="2026-09-17T04:00:00Z"))
        with self.assertRaisesRegex(sut.TrustRegistryAdapterError, "future-dated"): self.compile()

    def test_06_stale_observation_rejected(self):
        self.write_receipt(make_adapter_receipt(self.raw, observed_at="2026-09-01T00:00:00Z"))
        with self.assertRaisesRegex(sut.TrustRegistryAdapterError, "stale"): self.compile()

    def test_07_source_ref_allowlist_rejected(self):
        self.write_receipt(make_adapter_receipt(self.raw, source_ref="bank://wrong/1"))
        with self.assertRaisesRegex(sut.TrustRegistryAdapterError, "allowlist"): self.compile()

    def test_08_family_adapter_mismatch_rejected(self):
        self.write_receipt(make_adapter_receipt(self.raw, family="BANK"))
        with self.assertRaisesRegex(sut.TrustRegistryAdapterError, "source_family"): self.compile()

    def test_09_duplicate_source_id_rejected(self):
        (self.root / "source2.bin").write_bytes(b"two")
        (self.root / "receipt2.json").write_bytes(canonical(make_adapter_receipt(b"two", capture_id="capture-2")))
        self.manifest["captures"].append({"receipt_path":"receipt2.json","source_path":"source2.bin"})
        self.manifest_path.write_bytes(canonical(self.manifest))
        with self.assertRaisesRegex(sut.TrustRegistryAdapterError, "duplicate source_id"): self.compile()

    def test_10_reminted_fingerprint_rejected(self):
        self.write_receipt(make_adapter_receipt(self.raw, source_id="source-1"))
        (self.root / "receipt2.json").write_bytes(canonical(make_adapter_receipt(self.raw, source_id="source-2", capture_id="capture-2")))
        self.manifest["captures"].append({"receipt_path":"receipt2.json","source_path":"source.bin"})
        self.manifest_path.write_bytes(canonical(self.manifest))
        with self.assertRaisesRegex(sut.TrustRegistryAdapterError, "remint"): self.compile()

    def test_11_duplicate_json_key_rejected(self):
        self.manifest_path.write_bytes(b'{"schema":"x","schema":"y","generated_at":"2026-09-17T03:00:00Z","captures":[]}')
        with self.assertRaisesRegex(sut.TrustRegistryAdapterError, "duplicate JSON key"): self.compile()

    def test_12_float_and_nonfinite_json_rejected(self):
        for payload in (b'{"x":1.5}', b'{"x":NaN}'):
            with self.subTest(payload=payload):
                with self.assertRaises(sut.TrustRegistryAdapterError): sut.loads_strict(payload)

    def test_13_manifest_path_escape_rejected(self):
        self.manifest["captures"][0]["source_path"] = "../source.bin"
        self.manifest_path.write_bytes(canonical(self.manifest))
        with self.assertRaisesRegex(sut.TrustRegistryAdapterError, "capture root"): self.compile()

    def test_14_symlink_source_rejected(self):
        target = self.root / "real.bin"; target.write_bytes(self.raw)
        (self.root / "source.bin").unlink(); (self.root / "source.bin").symlink_to(target)
        with self.assertRaisesRegex(sut.TrustRegistryAdapterError, "non-symlink"): self.compile()

    def test_15_nonregular_source_rejected(self):
        (self.root / "source.bin").unlink(); (self.root / "source.bin").mkdir()
        with self.assertRaisesRegex(sut.TrustRegistryAdapterError, "regular"): self.compile()

    def test_16_read_drift_rejected(self):
        real_fstat = os.fstat
        calls = {"n": 0}
        def drifting(fd):
            st = real_fstat(fd); calls["n"] += 1
            if calls["n"] == 2:
                values = list(st)
                values[8] = values[8] + 1
                return os.stat_result(values)
            return st
        with mock.patch("os.fstat", side_effect=drifting):
            with self.assertRaisesRegex(sut.TrustRegistryAdapterError, "changed during read"): self.compile()

    def test_17_registry_tamper_fails_verification(self):
        registry, receipt = self.compile(); registry["sources"][0]["source_ref"] = "provider://tampered"
        self.assertFalse(sut.verify_outputs(self.manifest_path, self.root, REGISTRY_KEY, self.resolver, registry, receipt))

    def test_18_receipt_tamper_fails_verification(self):
        registry, receipt = self.compile(); receipt["source_count"] = 99
        self.assertFalse(sut.verify_outputs(self.manifest_path, self.root, REGISTRY_KEY, self.resolver, registry, receipt))

    def test_19_wrong_registry_key_fails_verification(self):
        registry, receipt = self.compile()
        self.assertFalse(sut.verify_outputs(self.manifest_path, self.root, b"wrong-registry-key-but-long-enough", self.resolver, registry, receipt))

    def test_20_cli_env_compile_then_verify_and_create_exclusive(self):
        out_reg = self.root / "registry.json"; out_receipt = self.root / "signing.json"
        env = {
            "REWARD_SETTLEMENT_TRUST_KEY": REGISTRY_KEY.decode(),
            "REWARD_SETTLEMENT_PROVIDER_ADAPTER_KEY": ADAPTER_KEY.decode(),
        }
        args = ["--manifest",str(self.manifest_path),"--capture-root",str(self.root),"--registry",str(out_reg),"--receipt",str(out_receipt)]
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(sut.main(args), 0)
            self.assertEqual(sut.main(args + ["--verify"]), 0)
            with self.assertRaises(SystemExit): sut.main(args)

    def test_21_cli_fd_registry_key(self):
        out_reg = self.root / "registry-fd.json"; out_receipt = self.root / "signing-fd.json"
        rfd, wfd = os.pipe(); os.write(wfd, REGISTRY_KEY); os.close(wfd)
        env = {"REWARD_SETTLEMENT_PROVIDER_ADAPTER_KEY": ADAPTER_KEY.decode()}
        args = ["--manifest",str(self.manifest_path),"--capture-root",str(self.root),"--registry",str(out_reg),"--receipt",str(out_receipt),"--registry-key-fd",str(rfd)]
        try:
            with mock.patch.dict(os.environ, env, clear=False): self.assertEqual(sut.main(args), 0)
        finally:
            os.close(rfd)
        self.assertTrue(out_reg.exists()); self.assertTrue(out_receipt.exists())

    def test_22_output_pair_rolls_back_if_second_exists(self):
        out_reg = self.root / "registry.json"; out_receipt = self.root / "signing.json"; out_receipt.write_text("occupied")
        env = {"REWARD_SETTLEMENT_TRUST_KEY":REGISTRY_KEY.decode(),"REWARD_SETTLEMENT_PROVIDER_ADAPTER_KEY":ADAPTER_KEY.decode()}
        args=["--manifest",str(self.manifest_path),"--capture-root",str(self.root),"--registry",str(out_reg),"--receipt",str(out_receipt)]
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(SystemExit): sut.main(args)
        self.assertFalse(out_reg.exists()); self.assertEqual(out_receipt.read_text(),"occupied")

    def test_23_adapter_signature_cannot_be_rebound_to_observed_at(self):
        bad = dict(self.receipt); bad["observed_at"] = "2026-09-17T02:30:00Z"
        self.write_receipt(bad)
        with self.assertRaisesRegex(sut.TrustRegistryAdapterError, "HMAC"): self.compile()

    def test_24_adapter_signature_cannot_be_rebound_to_ref_or_digest(self):
        for field, value in (("source_ref","provider://case/2"),("source_sha256","f"*64)):
            with self.subTest(field=field):
                bad=dict(self.receipt); bad[field]=value; self.write_receipt(bad)
                with self.assertRaises(sut.TrustRegistryAdapterError): self.compile()
                self.write_receipt(self.receipt)


if __name__ == "__main__": unittest.main()
