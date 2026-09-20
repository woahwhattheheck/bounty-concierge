from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT / "work" / "high-value" / "microg-580" / "verify_cast_candidate.py"


def load_oracle():
    spec = importlib.util.spec_from_file_location("microg_580_cast_oracle", ORACLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def passing_tree(tmp_path: Path, oracle) -> Path:
    write(tmp_path, oracle.FILES["controller_aidl"], """
interface ICastDeviceController {
  oneway void connect() = 16;
  oneway void setListener(ICastDeviceControllerListener listener) = 17;
  oneway void unregisterListener() = 18;
}
""")
    write(tmp_path, oracle.FILES["listener_aidl"], """
interface ICastDeviceControllerListener {
  void onConnectedWithResult(int statusCode) = 13;
}
""")
    write(tmp_path, oracle.FILES["service"], """
class CastDeviceControllerService {
  static final Feature[] CAST_FEATURES = new Feature[]{new Feature("cast_cxless", 1L)};
  void f() {
    Object x = GmsService.CAST, GmsService.CAST_API;
    Object requested = request.apiFeatures;
    ConnectionInfo info = new ConnectionInfo();
    info.features = CAST_FEATURES;
    callback.onPostInitCompleteWithConnectionInfo(0, null, info);
  }
}
""")
    write(tmp_path, oracle.FILES["context"], """
class CastContextImpl {
  void f() {
    router.registerMediaRouterCallbackImpl(bundle, callback);
    router.addCallback(bundle, 1);
    if (entry.getKey().startsWith(defaultCategory)) { }
  }
}
""")
    write(tmp_path, oracle.FILES["router"], """
class MediaRouterCallbackImpl {
  void f() {
    if (defaultSessionProvider == null) return;
    if (!(session instanceof SessionImpl)) return;
    Object chosen = routeInfoExtras != null ? routeInfoExtras : extras;
  }
}
""")
    write(tmp_path, oracle.FILES["impl"], """
class CastDeviceControllerImpl {
  void setup() { binder.linkToDeath(recipient, 0); }
  void dead() { if (deadBinder != this.listenerBinder) return; }
  public void connect() { }
  public void setListener(ICastDeviceControllerListener listener) { }
  public void disconnect() {
    disconnectChromecast();
    clearListener();
  }
  public void rawMessageReceived(Object message, Long requestId) {
    // onSendMessageSuccess is intentionally named in a comment only.
    onTextMessageReceived("ns", "payload");
  }
  public void sendMessage(String namespace, String message, long requestId) {
    chromecast.sendRawRequest(namespace, message, requestId);
    onSendMessageSuccess("", requestId);
  }
}
""")
    return tmp_path


def test_passing_contract_ignores_callback_name_in_comment(tmp_path):
    oracle = load_oracle()
    root = passing_tree(tmp_path, oracle)
    failures, warnings = oracle.check(root)
    assert failures == []
    assert warnings == ["SUPPORTING FIX ABSENT: #3554 Cast framework ModuleDescriptor"]


def test_inbound_send_success_invocation_is_rejected(tmp_path):
    oracle = load_oracle()
    root = passing_tree(tmp_path, oracle)
    path = root / oracle.FILES["impl"]
    content = path.read_text(encoding="utf-8").replace(
        'onTextMessageReceived("ns", "payload");',
        'onSendMessageSuccess("", requestId);\n    onTextMessageReceived("ns", "payload");',
    )
    path.write_text(content, encoding="utf-8")
    failures, _ = oracle.check(root)
    assert "MESSAGE CONTRACT: inbound rawMessageReceived must not invoke send success" in failures


def test_disconnect_clear_before_transport_teardown_is_rejected(tmp_path):
    oracle = load_oracle()
    root = passing_tree(tmp_path, oracle)
    path = root / oracle.FILES["impl"]
    content = path.read_text(encoding="utf-8").replace(
        "disconnectChromecast();\n    clearListener();",
        "clearListener();\n    disconnectChromecast();",
    )
    path.write_text(content, encoding="utf-8")
    failures, _ = oracle.check(root)
    assert "ORDERING: disconnect must tear down Cast before clearing listener" in failures


def test_direct_caller_feature_reflection_is_rejected(tmp_path):
    oracle = load_oracle()
    root = passing_tree(tmp_path, oracle)
    path = root / oracle.FILES["service"]
    content = path.read_text(encoding="utf-8").replace(
        "info.features = CAST_FEATURES;",
        "info.features = request.apiFeatures;",
    )
    path.write_text(content, encoding="utf-8")
    failures, _ = oracle.check(root)
    assert (
        "FEATURE CONTRACT: caller-controlled apiFeatures/defaultFeatures must not be advertised directly"
        in failures
    )


def test_direct_default_feature_reflection_is_rejected(tmp_path):
    oracle = load_oracle()
    root = passing_tree(tmp_path, oracle)
    path = root / oracle.FILES["service"]
    content = path.read_text(encoding="utf-8").replace(
        "info.features = CAST_FEATURES;",
        "info.features = request.defaultFeatures;",
    )
    path.write_text(content, encoding="utf-8")
    failures, _ = oracle.check(root)
    assert (
        "FEATURE CONTRACT: caller-controlled apiFeatures/defaultFeatures must not be advertised directly"
        in failures
    )


def test_request_feature_inspection_is_allowed_when_advertisement_is_server_owned(tmp_path):
    oracle = load_oracle()
    root = passing_tree(tmp_path, oracle)
    failures, _ = oracle.check(root)
    assert not any("FEATURE CONTRACT" in failure for failure in failures)


def test_missing_implementation_owned_feature_set_is_rejected(tmp_path):
    oracle = load_oracle()
    root = passing_tree(tmp_path, oracle)
    path = root / oracle.FILES["service"]
    content = path.read_text(encoding="utf-8")
    content = content.replace(
        '  static final Feature[] CAST_FEATURES = new Feature[]{new Feature("cast_cxless", 1L)};\n',
        "",
    )
    path.write_text(content, encoding="utf-8")
    failures, _ = oracle.check(root)
    assert "MISSING INVARIANT: service declares implementation-owned Cast FEATURES" in failures
