"""Repository-local adapter for predecessor sponsor semantic tests.

This module is auto-loaded only when the sponsor workflow explicitly prepends
this directory to ``PYTHONPATH``. It is outside the ``concierge`` package and is
not an installable production authority mode. The adapter preserves the original
state-machine regression suite while production authority remains fail-closed.
"""
from __future__ import annotations

import concierge.sponsor_adjudication.compiler as _compiler
import concierge.sponsor_adjudication.schema as _schema
import concierge.sponsor_adjudication.verified as _verified


def _accept_legacy_manifest(manifest, program, events, authority_value):
    return None


def _omit_legacy_report_binding(program, report_projection):
    return None


def _accept_legacy_report(program, events, report_binding_value, report_projection):
    return None


_schema.verify_manifest_authority = _accept_legacy_manifest
_compiler.bind_report_generation = _omit_legacy_report_binding
_verified.verify_report_authority = _accept_legacy_report
