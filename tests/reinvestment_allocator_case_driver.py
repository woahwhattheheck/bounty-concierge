from __future__ import annotations
import datetime
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import select
import sys
import types

CASE = sys.argv[1]
ROOT = Path(sys.argv[2])
MARKER = ROOT / "provider.marker"
N_HEX = sys.argv[3]
D_HEX = sys.argv[4]
ATTACKER_N_HEX = sys.argv[5]
ATTACKER_D_HEX = sys.argv[6]
KEY_ID = "owner-rsa-2026-09"
JSON_DUMPS = json.dumps
PROVIDER = "owner-review-host"
PRINCIPAL = "22" * 32

def canonical(value):
    return JSON_DUMPS(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
def fingerprint(n_hex):
    return hashlib.sha256(bytes.fromhex(n_hex) + b"\0" + b"65537").hexdigest()
def sign(core, n_hex=N_HEX, d_hex=D_HEX):
    n = int(n_hex, 16); d = int(d_hex, 16)
    digest = hashlib.sha256(b"realized-reinvestment-commercial-evidence-authority/v2\0" + canonical(core)).digest()
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + digest
    width = (n.bit_length() + 7) // 8
    encoded = b"\0\1" + b"\xff" * (width - len(digest_info) - 3) + b"\0" + digest_info
    return pow(int.from_bytes(encoded, "big"), d, n).to_bytes(width, "big").hex()
def sha(value): return hashlib.sha256(canonical(value)).hexdigest()
def configure(n_hex=N_HEX, key_id=KEY_ID, provider=PROVIDER, principal=PRINCIPAL):
    os.environ.update({
        "REALIZED_REINVESTMENT_AUTHORITY_RSA_MODULUS_HEX": n_hex,
        "REALIZED_REINVESTMENT_AUTHORITY_RSA_KEY_ID": key_id,
        "REALIZED_REINVESTMENT_AUTHORIZED_PROVIDER": provider,
        "REALIZED_REINVESTMENT_AUTHORIZED_PRINCIPAL_SHA256": principal,
        "ZTE_PROVIDER_MARKER": str(MARKER),
    })

if CASE != "late-config":
    configure()
sys.path.insert(0, str(ROOT))
from concierge import reinvestment_allocator as ra
if CASE == "late-config":
    configure()

manifest = [{"repo":"acme/widgets","pr":17,"expected_head_sha":"3"*40}]
bindings = [{"repo":"acme/widgets","pr":17,"transfer_id":"real-transfer-1"}]
effort = {"schema_version":1,"items":[{"repo":"acme/widgets","pr":17,"active_minutes":60}]}
taxonomy = {"schema":"realized-reinvestment-taxonomy/v1","version":1,"mappings":[{"repo":"acme/widgets","pr":17,"family":"bounty-fix"}]}
taxonomy["taxonomy_sha256"] = sha(taxonomy)
policy = {"schema":"realized-reinvestment-policy/v1","version":1,"minimum_samples":1,"minimum_nonzero_cash_samples":1,"minimum_median_rtc_per_hour":"1","maximum_family_capacity_bps":10000}
policy["policy_sha256"] = sha(policy)
wallet = "wallet-1"

def authority(captured_at=None, n_hex=N_HEX, d_hex=D_HEX, key_id=KEY_ID, provider=PROVIDER, principal=PRINCIPAL):
    if captured_at is None:
        captured_at = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = {
        "schema_version":2,
        "purpose":"realized-reinvestment-commercial-evidence-authority/v2",
        "algorithm":"rsa-pkcs1v15-sha256",
        "key_id":key_id,
        "public_key_sha256":fingerprint(n_hex),
        "provider":provider,
        "principal_sha256":principal,
        "captured_at":captured_at,
        "scope_sha256":ra.commercial_evidence_scope_sha256(manifest, bindings, effort, wallet=wallet),
    }
    result = dict(core); result["signature_hex"] = sign(core, n_hex, d_hex)
    return result

def compile_one(auth=None, effort_value=None, taxonomy_value=None):
    return ra.compile_reinvestment_review(
        manifest, bindings, effort if effort_value is None else effort_value,
        authority() if auth is None else auth,
        taxonomy if taxonomy_value is None else taxonomy_value,
        policy, 60, wallet=wallet,
    )

def emit(value):
    print(JSON_DUMPS(value, sort_keys=True))

try:
    if CASE == "valid":
        receipt = compile_one()
        emit({
            "schema": receipt["schema"],
            "state": receipt["families"][0]["state"],
            "authority": receipt["authority"]["commercial_attribution_effort_authority"],
            "isolated": receipt["authority"]["isolated_provider_composition"],
            "symmetric_secret": receipt["authority"]["symmetric_signing_secret_present_in_review_process"],
            "integrity": ra.verify_receipt_integrity_only(receipt),
            "current": ra.verify_reinvestment_receipt_current(receipt, manifest, bindings, effort, receipt["authority"]["commercial_evidence_authority"], taxonomy, policy, 60, wallet=wallet),
            "marker": MARKER.exists(),
        })
    elif CASE == "closure-container-poison":
        import types as _types
        seen = set(); trust_dicts = []
        def walk(value, depth=0):
            if id(value) in seen or depth > 10:
                return
            seen.add(id(value))
            if isinstance(value, dict):
                if any(isinstance(k, str) and k.startswith("REALIZED_REINVESTMENT_") for k in value):
                    trust_dicts.append(value)
                for nested in value.values(): walk(nested, depth + 1)
            elif isinstance(value, _types.FunctionType) and value.__closure__:
                for cell in value.__closure__:
                    try: nested = cell.cell_contents
                    except ValueError: continue
                    walk(nested, depth + 1)
            elif isinstance(value, (tuple, list, set, frozenset)):
                for nested in value: walk(nested, depth + 1)
        walk(ra.compile_reinvestment_review)
        if trust_dicts:
            trust_dicts[0]["REALIZED_REINVESTMENT_AUTHORITY_RSA_MODULUS_HEX"] = ATTACKER_N_HEX
        receipt = compile_one()
        emit({"trust_dict_count":len(trust_dicts),"schema":receipt["schema"],"marker":MARKER.exists()})
    elif CASE == "post-import-rebind":
        compile_fn = ra.compile_reinvestment_review
        ra._verify_evidence_authority = lambda *a, **k: {}
        ra._host_config = lambda: (b"attacker", "attacker", "0"*64)
        ra._utc_now = lambda: datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
        ra._core = types.SimpleNamespace(compile_reinvestment_review=lambda *a, **k: {"schema":"fake"})
        ra._worker_path = "/tmp/attacker.py"
        ra._MAX_JSON_BYTES = 1
        ra._MAX_WORKER_SECONDS = 0
        ra._RECEIPT_SCHEMA = "attacker-schema"
        ra._SHA256_RE = None
        ra.ReinvestmentInputError = RuntimeError
        os.posix_spawn = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("patched"))
        os.pipe = lambda: (_ for _ in ()).throw(RuntimeError("patched"))
        os.read = lambda *a: b'{"protocol":"realized-reinvestment-isolated-worker/v1","receipt":{"schema":"realized-reinvestment-review/v4"}}'
        os.write = lambda *a: len(a[1])
        os.set_blocking = lambda *a: (_ for _ in ()).throw(RuntimeError("patched"))
        os.waitpid = lambda *a: (_ for _ in ()).throw(RuntimeError("patched"))
        os.kill = lambda *a: (_ for _ in ()).throw(RuntimeError("patched"))
        select.select = lambda *a, **k: ([], [], [])
        json.dumps = lambda *a, **k: "{}"
        json.loads = lambda *a, **k: {"attacker": True}
        os.environ["REALIZED_REINVESTMENT_AUTHORITY_RSA_MODULUS_HEX"] = ATTACKER_N_HEX
        receipt = compile_fn(manifest, bindings, effort, authority(), taxonomy, policy, 60, wallet=wallet)
        emit({"schema":receipt["schema"],"marker":MARKER.exists(),"isolated":receipt["authority"]["isolated_provider_composition"]})
    elif CASE == "forged-signature":
        auth = authority(); auth["signature_hex"] = "0" * len(auth["signature_hex"])
        try: compile_one(auth)
        except Exception as exc: emit({"blocked":True,"error":str(exc),"marker":MARKER.exists()})
        else: emit({"blocked":False,"marker":MARKER.exists()})
    elif CASE == "scope-change":
        auth = authority()
        changed = {"schema_version":1,"items":[{"repo":"acme/widgets","pr":17,"active_minutes":1}]}
        try: compile_one(auth, effort_value=changed)
        except Exception as exc: emit({"blocked":True,"error":str(exc),"marker":MARKER.exists()})
        else: emit({"blocked":False,"marker":MARKER.exists()})
    elif CASE == "stale":
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=301)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        try: compile_one(authority(old))
        except Exception as exc: emit({"blocked":True,"error":str(exc),"marker":MARKER.exists()})
        else: emit({"blocked":False,"marker":MARKER.exists()})
    elif CASE == "future":
        future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=60)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        try: compile_one(authority(future))
        except Exception as exc: emit({"blocked":True,"error":str(exc),"marker":MARKER.exists()})
        else: emit({"blocked":False,"marker":MARKER.exists()})
    elif CASE == "post-import-key-switch":
        os.environ["REALIZED_REINVESTMENT_AUTHORITY_RSA_MODULUS_HEX"] = ATTACKER_N_HEX
        os.environ["REALIZED_REINVESTMENT_AUTHORITY_RSA_KEY_ID"] = "attacker-key"
        auth = authority(n_hex=ATTACKER_N_HEX, d_hex=ATTACKER_D_HEX, key_id="attacker-key")
        try: compile_one(auth)
        except Exception as exc: emit({"blocked":True,"error":str(exc),"marker":MARKER.exists()})
        else: emit({"blocked":False,"marker":MARKER.exists()})
    elif CASE == "late-config":
        try: compile_one(authority())
        except Exception as exc: emit({"blocked":True,"error":str(exc),"marker":MARKER.exists()})
        else: emit({"blocked":False,"marker":MARKER.exists()})
    elif CASE == "core-tamper":
        core_path = ROOT / "concierge" / "_reinvestment_allocator_core.source"
        core_path.write_bytes(core_path.read_bytes() + b"\n# tamper\n")
        try: compile_one()
        except Exception as exc: emit({"blocked":True,"error":str(exc),"marker":MARKER.exists()})
        else: emit({"blocked":False,"marker":MARKER.exists()})
    elif CASE == "parent-provider-poison":
        fake = types.ModuleType("concierge.revenue_closeout")
        fake.build_closeout_queue = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("parent poison reached"))
        sys.modules["concierge.revenue_closeout"] = fake
        receipt = compile_one()
        emit({"schema":receipt["schema"],"marker":MARKER.exists()})
    elif CASE == "tampered-receipt":
        receipt = compile_one(); receipt["summary"]["review_state"] = "PAY_ME"
        emit({"integrity":ra.verify_receipt_integrity_only(receipt),"current":ra.verify_reinvestment_receipt_current(receipt,manifest,bindings,effort,authority(),taxonomy,policy,60,wallet=wallet)})
    elif CASE == "planning-inputs":
        changed = dict(taxonomy); changed["mappings"] = [dict(taxonomy["mappings"][0])]; changed["mappings"][0]["family"] = "alternate"; changed.pop("taxonomy_sha256"); changed["taxonomy_sha256"] = sha(changed)
        receipt = compile_one(taxonomy_value=changed)
        emit({"family":receipt["families"][0]["family"],"authenticated":receipt["authority"]["taxonomy_policy_authenticated_as_owner"]})
    elif CASE == "worker-import":
        path = ROOT / "concierge" / "_reinvestment_allocator_worker.py"
        spec = importlib.util.spec_from_file_location("worker_import_test", path)
        module = importlib.util.module_from_spec(spec)
        try: spec.loader.exec_module(module)
        except Exception as exc: emit({"blocked":True,"type":type(exc).__name__})
        else: emit({"blocked":False})
    elif CASE == "core-import":
        try: importlib.import_module("concierge._reinvestment_allocator_core")
        except Exception as exc: emit({"blocked":True,"type":type(exc).__name__})
        else: emit({"blocked":False})
    elif CASE == "retired-helper-import":
        outcomes = []
        for name in ("concierge._reinvestment_allocator_worker_authority", "concierge._reinvestment_allocator_worker_runtime"):
            try: importlib.import_module(name)
            except Exception as exc: outcomes.append(type(exc).__name__)
            else: outcomes.append("IMPORTED")
        emit({"blocked": all(value != "IMPORTED" for value in outcomes), "types": outcomes})
    else:
        raise RuntimeError("unknown case")
except Exception as exc:
    emit({"driver_error":type(exc).__name__,"error":str(exc)})
