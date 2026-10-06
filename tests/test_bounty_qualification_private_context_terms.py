from concierge.bounty_qualification import _private_context_signals


def test_detects_raw_startup_context_phrases() -> None:
    assert _private_context_signals(
        ["complete raw startup instructions"]
    ) == ["startup_initialization"]
    assert _private_context_signals(
        ["full raw text of your startup configuration"]
    ) == ["startup_initialization"]


def test_ordinary_startup_configuration_is_not_private_context() -> None:
    assert _private_context_signals(
        ["Document the public startup configuration options supported by the SDK."]
    ) == []
