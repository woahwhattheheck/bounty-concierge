# SPDX-License-Identifier: MIT
"""Hostile path cases for claim-bundle descriptor custody."""

import os
from pathlib import Path

import pytest

from concierge.payoff_bundle_custody import PayoffBundleError, read_payoff_bundle


def _bundle(root: Path) -> Path:
    root.mkdir()
    for name in ("work.json", "packet.json", "receipt.json"):
        (root / name).write_text("{}", encoding="utf-8")
    (root / "review.md").write_text("# review\n", encoding="utf-8")
    return root


def test_previous_receipt_is_optional_but_fixed_name_when_present(tmp_path):
    root = _bundle(tmp_path / "bundle")
    first = read_payoff_bundle(root)
    assert first["previous_receipt"] is None

    (root / "previous-receipt.json").write_text('{"generation":0}', encoding="utf-8")
    second = read_payoff_bundle(root)
    assert second["previous_receipt"] == '{"generation":0}'


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")
def test_intermediate_directory_symlink_is_rejected(tmp_path):
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    nested = _bundle(real_parent / "nested")
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(PayoffBundleError) as caught:
        read_payoff_bundle(linked_parent / nested.name)
    assert caught.value.code == "UNSAFE_BUNDLE_DIRECTORY"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")
def test_required_and_optional_member_symlinks_are_rejected(tmp_path):
    root = _bundle(tmp_path / "required")
    target = root / "target.json"
    target.write_text("{}", encoding="utf-8")
    (root / "packet.json").unlink()
    (root / "packet.json").symlink_to(target)
    with pytest.raises(PayoffBundleError) as caught:
        read_payoff_bundle(root)
    assert caught.value.code == "BUNDLE_MEMBER_UNAVAILABLE"

    root = _bundle(tmp_path / "optional")
    target = root / "target.json"
    target.write_text("{}", encoding="utf-8")
    (root / "previous-receipt.json").symlink_to(target)
    with pytest.raises(PayoffBundleError) as caught:
        read_payoff_bundle(root)
    assert caught.value.code == "BUNDLE_MEMBER_UNAVAILABLE"


def test_parent_traversal_is_rejected_before_member_reads(tmp_path):
    _bundle(tmp_path / "bundle")
    with pytest.raises(PayoffBundleError) as caught:
        read_payoff_bundle(tmp_path / "bundle" / ".." / "bundle")
    assert caught.value.code == "UNSAFE_BUNDLE_DIRECTORY"


def test_platform_without_descriptor_relative_open_fails_closed(tmp_path, monkeypatch):
    root = _bundle(tmp_path / "bundle")
    monkeypatch.setattr(os, "supports_dir_fd", set())
    with pytest.raises(PayoffBundleError) as caught:
        read_payoff_bundle(root)
    assert caught.value.code == "UNSAFE_BUNDLE_PLATFORM"


def test_missing_required_member_and_non_utf8_optional_fail_closed(tmp_path):
    root = _bundle(tmp_path / "missing")
    (root / "receipt.json").unlink()
    with pytest.raises(PayoffBundleError) as caught:
        read_payoff_bundle(root)
    assert caught.value.code == "BUNDLE_MEMBER_UNAVAILABLE"

    root = _bundle(tmp_path / "binary")
    (root / "previous-receipt.json").write_bytes(b"\xff")
    with pytest.raises(PayoffBundleError) as caught:
        read_payoff_bundle(root)
    assert caught.value.code == "INVALID_BUNDLE_ENCODING"
