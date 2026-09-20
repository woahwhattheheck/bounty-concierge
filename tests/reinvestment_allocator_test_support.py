# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "concierge"
DRIVER_SOURCE = ROOT / "tests" / "reinvestment_allocator_case_driver.py"

# Test-only RSA keys. Production contains only a boot-pinned public modulus.
TEST_N = 'e285a516d763b31ff68ecb2e2943e15b91f62ab21f1b8d0aa84887d53690a28f871513c498abfa2b0f3af91807bc5ae40fefc0b395d1d7765a9d6f4a39bc70c68282cdf700c40a0dfb34a9f00be14688219d4ed987dc6b2502aa17c8296e04502a31a918aa64fb09ffb0c8b8f3378f0dc9b520315b80ad4f9b0036829599ff31b03df48bcbc7620c0c7178b4fdb0b3c93c02384bb0b315b205845b741572d56bc2c7af5ec24e5b5da2599304d6a80bcc0294ffb82e0466c1761bf14d455f5b3e50eea9872f8a790b259aa9733a5384e7a2f154a77cf0ae6260ef338682aef97834352c62ed1904b0dedc7fd2bb041278faf2d56ebe5a353eacaf409d41771ff3'
TEST_D = '1c4b4935b32db6e01531fcdb05387f5baabdaed394e32218e4b03a973d3b8fb291d2e6273652b4eacffa33c6f6a06651fa539e24506067a356ea374e742bbf826c26de872dc74ee234307dd1880a597f0383dca77b3ae2ff3a77f8847df849fda1679691882718dc44ddd61493fcaa97b44c357b34ccbbd927a93d25ce7defcc0ee76529423b64f19c119f870f40fb59bc129509f912dfa768b01d883355b428a0fd1fd334b67b2de3f74a9592dab07d8c1854c8507013ec50911483bb1c1fb982ae04687c24b1d769f84e0945af1f26dffa5f3eac3645e8501c5c1693e9f620586e1a3a1be68dd9a236c08f350d38a48b619bad4ce0009318c8eb62b92aa9d1'
ATTACKER_N = 'cf9eaefdefbdcfcef218903eb8640fcba7fce857b0d70cc8ccd479da9bc7c578156648f18ffecc00ca19c6404180bbb9b1042dc456e596f83d6df01f210c27f8920e9bb8114f7b3d2f910e3eda5db09f5629968ff996ae0e496a0500bf6f18b04a4dc3c3167f7ef797e6e252925567bd7ff1890eba0558135c311c8e0f5102b9f2692d0bf4f618125bcfe6eb329566becbb4eacba678c14ad5551b7c6d735a4b0b22320981d4518cc807cd06a4f836b4e3c2e560bbe815956a4e842566a13fef263ba2ea588cd9a5ecd7b1f5a81169fee903a1dc94540918698577f727f96b34400b1d6f3a793c4308944fcdb155b39fce8dcef2aba0490bb8107b29811d2f53'
ATTACKER_D = '38179d1b8a2bf3faeb396baf264e125e695250cb70418978aeae3105b8d4ae2b154cff24144d387c761da48c33a63e119b1b8fe9ddb845a367a54e1c7ae737dd5648901598c7aa7a0b61db416be9a810444d141a6dca21d846495874ad43ac9de455423070e19d41f34e33932f69d78f4103d68d9e00579d53690957f01768eeab3fa6bd5da61ffb80a6a3295274b5296d55504d6bb2b7a9739dfefe74311bb79819f5722b9919ff23071b04030a855b6c266cfefafdc5ced227e6b1f8312027d8bcd119b36cbd37c24deb1be3d10b5b366a2a84ed5baecb3daaadb3530ac84e102601a907bc092f8be13a152db0c199f55660f244c657e3e3e0532029de99e1'

CLOSEOUT_STUB = '\nfrom __future__ import annotations\nimport os\nfrom pathlib import Path\nmarker = os.environ.get("ZTE_PROVIDER_MARKER")\nif marker:\n    Path(marker).write_text("provider-loaded", encoding="utf-8")\nclass RevenueCloseoutError(RuntimeError): pass\nclass RevenueCloseoutInputError(ValueError): pass\ndef build_closeout_queue(manifest, max_pages=10):\n    return {"schema_version": 1, "items": manifest, "max_pages": max_pages}\n'
SETTLEMENT_STUB = '\nfrom __future__ import annotations\nclass PayoutLookupError(RuntimeError): pass\nclass RevenueSettlementInputError(ValueError): pass\nclass RevenueSettlementEvidenceError(ValueError): pass\ndef _query_canonical_history(wallet):\n    return ([{"transfer_id": "real-transfer-1"}], wallet)\n'
RUE_STUB = '\nfrom __future__ import annotations\nimport hashlib, json\nclass RealizedUnitEconomicsInputError(ValueError): pass\ndef _canonical(value):\n    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")\ndef _sha(value): return hashlib.sha256(_canonical(value)).hexdigest()\ndef compile_realized_unit_economics(closeout, history, bindings, effort, *, wallet, history_wallet, history_source):\n    cash = [{"repo":"acme/widgets","pr":17,"state":"MERGED","cash_status":"verified_paid","verified_cash_rtc":"10","payment_evidence_sha256s":["a"*64]}]\n    effort_scope = [{"repo":"acme/widgets","pr":17,"active_minutes":60}]\n    payload = {\n        "schema_version": 1,\n        "wallet": wallet,\n        "history_source": "queried_wallet",\n        "scope_sha256": _sha({"cash": cash, "effort": effort_scope}),\n        "summary": {\n            "currency":"RTC","verified_cash_total":"10","active_minutes_total":60,\n            "realized_rtc_per_hour_estimate":"10","fully_paid_items":1,\n            "partially_paid_items":0,"zero_verified_cash_items":0,"item_count":1,\n            "scope_complete":True,"cash_basis":"revenue_settlement_wallet_evidence",\n            "effort_basis":"operator_active_minutes","fx_conversion":False,\n            "accounting_revenue_claim":False,"tax_claim":False,\n            "payout_or_transfer_authority":False,\n        },\n        "ranking":[{"rank":1,"repo":"acme/widgets","pr":17,"realized_rtc_per_hour_estimate":"10"}],\n        "items":[{\n            "repo":"acme/widgets","pr":17,"state":"MERGED","cash_status":"verified_paid",\n            "verified_cash_rtc":"10","active_minutes":60,\n            "realized_rtc_per_hour_estimate":"10","payment_evidence_sha256s":["a"*64],\n        }],\n    }\n    payload["receipt_sha256"] = _sha(payload)\n    return payload\ndef verify_receipt(receipt):\n    if type(receipt) is not dict: return False\n    digest = receipt.get("receipt_sha256")\n    body = dict(receipt); body.pop("receipt_sha256", None)\n    return type(digest) is str and digest == _sha(body)\n'


class ReinvestmentCaseBase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        package = self.root / "concierge"
        package.mkdir()
        (package / "__init__.py").write_text("# isolated test package\n", encoding="utf-8")
        for name in ('reinvestment_allocator.py', '_reinvestment_allocator_api.py', '_reinvestment_allocator_transport.py', '_reinvestment_allocator_cli.py', '_reinvestment_allocator_worker.py', '_reinvestment_allocator_reload_guard.py', '_reinvestment_allocator_core.source'):
            shutil.copy2(MODULE_DIR / name, package / name)
        # Local development may use a deliberately tiny provider/core fixture.
        # Hosted CI leaves this unset and therefore exercises the exact checked-in
        # core blob pinned by the production worker.
        if os.environ.get("ZTE_TEST_CORE_OVERRIDE") == "1":
            core_path = package / "_reinvestment_allocator_core.source"
            core_bytes = core_path.read_bytes()
            fixture_sha = hashlib.sha1(
                b"blob " + str(len(core_bytes)).encode("ascii") + b"\x00" + core_bytes
            ).hexdigest()
            constants_path = package / "_reinvestment_allocator_worker.py"
            constants = constants_path.read_text(encoding="utf-8")
            constants = constants.replace(
                '5731fd0652bc94b20d1f3b2de48628f633f550a9', fixture_sha
            )
            constants_path.write_text(constants, encoding="utf-8")
        (package / "revenue_closeout.py").write_text(CLOSEOUT_STUB, encoding="utf-8")
        (package / "revenue_settlement.py").write_text(SETTLEMENT_STUB, encoding="utf-8")
        (package / "realized_unit_economics.py").write_text(RUE_STUB, encoding="utf-8")
        self.driver = self.root / "driver.py"
        shutil.copy2(DRIVER_SOURCE, self.driver)

    def tearDown(self):
        self.temp.cleanup()

    def run_case(self, case):
        command = [sys.executable]
        if sys.flags.optimize:
            command.append("-O")
        command.extend([
            str(self.driver), case, str(self.root), TEST_N, TEST_D, ATTACKER_N, ATTACKER_D,
        ])
        env = dict(os.environ)
        for key in tuple(env):
            if key.startswith("REALIZED_REINVESTMENT_") or key == "ZTE_PROVIDER_MARKER":
                env.pop(key, None)
        completed = subprocess.run(
            command,
            cwd=self.root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stderr={completed.stderr}\nstdout={completed.stdout}",
        )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertTrue(lines, msg=f"no JSON output; stderr={completed.stderr}")
        result = json.loads(lines[-1])
        self.assertNotIn("driver_error", result, msg=result)
        return result
