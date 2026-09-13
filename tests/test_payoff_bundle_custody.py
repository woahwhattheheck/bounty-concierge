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
