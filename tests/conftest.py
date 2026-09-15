"""Pytest-only harness for predecessor unsigned sponsor semantic modules.

Production sponsor authority has no unsigned mode. Two predecessor semantic test
files intentionally exercise the state machine without constructing host HMAC
fixtures for every mutation. Scope the compatibility adapter to those exact files
for the lifetime of each test and expose the same adapter to their child CLI
processes through an exact test-only ``PYTHONPATH`` entry.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import concierge.sponsor_adjudication.compiler as _compiler
import concierge.sponsor_adjudication.schema as _schema
import concierge.sponsor_adjudication.verified as _verified

_LEGACY_FILES = {
    "test_sponsor_adjudication.py",
    "test_sponsor_adjudication_hardening.py",
}


def _accept_legacy_manifest(manifest, program, events, authority_value):
    return None


def _omit_legacy_report_binding(program, report_projection):
    return None


def _accept_legacy_report(program, events, report_binding_value, report_projection):
    return None


@pytest.fixture(autouse=True)
def _legacy_sponsor_semantic_harness(request, monkeypatch):
    module = getattr(request, "module", None)
    if module is None or not getattr(module, "__file__", None):
        return

    tests_dir = Path(__file__).resolve().parent
    module_path = Path(module.__file__).resolve()
    if module_path.parent != tests_dir or module_path.name not in _LEGACY_FILES:
        return

    monkeypatch.setattr(_schema, "verify_manifest_authority", _accept_legacy_manifest)
    monkeypatch.setattr(_compiler, "bind_report_generation", _omit_legacy_report_binding)
    monkeypatch.setattr(_verified, "verify_report_authority", _accept_legacy_report)

    repo_root = tests_dir.parent
    adapter_dir = tests_dir / "sponsor_adjudication_unsigned_site"
    path_entries = [str(adapter_dir), str(repo_root)]
    existing = os.environ.get("PYTHONPATH")
    if existing:
        path_entries.append(existing)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(path_entries))
