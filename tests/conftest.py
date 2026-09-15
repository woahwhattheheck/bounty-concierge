from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from concierge import payoff_path_gate as gate
from concierge import payoff_path_gate_core as core
from concierge import payoff_path_policy_authority as authority

# Test-only RSA-2048 private fixture. Runtime code receives only the public N/e.
_TEST_RSA_N_HEX = (
    "aff7a78a9d3f1d170b0f27c775a6b5b776bb6eb7f09edbecebfbd12f19e53271"
    "3499c28a82c50cd048a7a5c58a8facacedaa21f8c2763beb07a63032a191e451"
    "5cf0b123d0ec576f9e990d693def17f8075cfa8ee87875eaef16371efef7da06"
    "b5462ae94ea4397308cce0c5c2b965a72566e970984a3ce08060b1f31e64fed0"
    "5751a91ff2eb16696e535f38723bae847cf8398bec3619951232dde3d7de822a5"
    "21a45c885042dfa2f16d82825ffa4798e1cd496b46a0a071ca4fa18b1095c458"
    "8ce392f9d8d2d61ffc5a76d54d91b3e664ef817e9e917c8a8149d05174950653"
    "a62b398cb9ed48115fdf0d4c9a9d66212837687896a2fbf5a66f8464ac7d113"
)
_TEST_RSA_D_HEX = (
    "bf6a81dba1fceb2d608a61409d6acbcc2c6197a9dfc5ef2c0ff32334e97bb91e"
    "0c3f76b02b05cf4fd8b905a042d4d231b56c4252d2b6c0572feaf2b876540d84"
    "5f1034a1916886c9ea419e4fc2b4e3e3459f39b005e783de1c56937f7ee67e9f"
    "ed7121721f1490a9e2037c3ef94e53eb39747406574dea6061f0cda279ddc33b6"
    "f4f3ee07e2fb487519a73ab6ee02c444ef9198924b119bc85e0e652692abfe83"
    "802492939402082577fcef7523224084e5f84e4aa316e4ccea3aaabc8d1d13bd9"
    "1bb1bd89eaf3465cca9cd5fe033850d6379384d7ed0f7fba15d3e44d033c19c9"
    "dfa49ee10636e126372702f5ff0f2b6c2a743eac47d863f7750ef5b65e881"
)
_TEST_RSA_E = 65537
_TEST_PROVIDER = "fixture-provider"
_TEST_PRINCIPAL = "9" * 64
_DIGESTINFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _canonical_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _signature(payload):
    modulus = int(_TEST_RSA_N_HEX, 16)
    private_exponent = int(_TEST_RSA_D_HEX, 16)
    key_bytes = (modulus.bit_length() + 7) // 8
    digest = hashlib.sha256(_canonical_bytes(payload)).digest()
    digest_info = _DIGESTINFO_PREFIX + digest
    encoded = (
        b"\x00\x01"
        + (b"\xff" * (key_bytes - len(digest_info) - 3))
        + b"\x00"
        + digest_info
    )
    signed = pow(int.from_bytes(encoded, "big"), private_exponent, modulus)
    return signed.to_bytes(key_bytes, "big").hex()


def _authorize(document, monkeypatch):
    captured = core._render_timestamp(datetime.now(timezone.utc).replace(microsecond=0))
    payload = {
        "purpose": authority.AUTHORITY_PURPOSE,
        "provider": _TEST_PROVIDER,
        "principal_sha256": _TEST_PRINCIPAL,
        "captured_at_utc": captured,
        "policy_scope_sha256": authority.policy_authority_scope_sha256(document),
    }
    monkeypatch.setenv(authority.CAPTURED_AT_ENV, captured)
    monkeypatch.setenv(authority.SIGNATURE_ENV, _signature(payload))


@pytest.fixture(autouse=True)
def _real_authority_for_v3_semantic_suite(request, monkeypatch):
    """Use real production verification in the legacy v3 semantic unit module only."""

    if not request.module.__name__.endswith("test_payoff_path_policy_v3"):
        yield
        return

    monkeypatch.setenv(authority.MODULUS_ENV, _TEST_RSA_N_HEX)
    monkeypatch.setenv(authority.EXPONENT_ENV, str(_TEST_RSA_E))
    monkeypatch.setenv(authority.PROVIDER_ENV, _TEST_PROVIDER)
    monkeypatch.setenv(authority.PRINCIPAL_ENV, _TEST_PRINCIPAL)

    real_compile = gate.compile_gate
    real_verify = gate.verify_gate

    def compile_authorized(document, *args, **kwargs):
        if type(document) is dict and document.get("schema") == gate.WORK_SCHEMA_V3:
            _authorize(document, monkeypatch)
        return real_compile(document, *args, **kwargs)

    def verify_authorized(document, *args, **kwargs):
        if type(document) is dict and document.get("schema") == gate.WORK_SCHEMA_V3:
            _authorize(document, monkeypatch)
        return real_verify(document, *args, **kwargs)

    monkeypatch.setattr(gate, "compile_gate", compile_authorized)
    monkeypatch.setattr(gate, "verify_gate", verify_authorized)
    yield
