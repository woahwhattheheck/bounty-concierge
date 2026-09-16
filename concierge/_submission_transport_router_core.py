# SPDX-License-Identifier: MIT
"""Verified loader for the renderer-free submission transport core.

The implementation is stored in deterministic Python source shards so every
normal import, ``importlib.reload`` and ``runpy.run_module`` execution rebuilds
the same renderer-free namespace.  The exact decoded source is length- and
SHA-256-bound before execution.
"""

from __future__ import annotations

import base64 as _base64
import hashlib as _hashlib
import linecache as _linecache

from ._submission_transport_core_source_00 import PART as _PART_00
from ._submission_transport_core_source_01 import PART as _PART_01
from ._submission_transport_core_source_02 import PART as _PART_02
from ._submission_transport_core_source_03 import PART as _PART_03
from ._submission_transport_core_source_04 import PART as _PART_04
from ._submission_transport_core_source_05 import PART as _PART_05
from ._submission_transport_core_source_06 import PART as _PART_06

# ``reload`` retains the module dictionary.  Remove the exact predecessor names
# before decoding so stale state from an older module object cannot survive.
globals().pop("_production_receipt", None)
globals().pop("_render_production_receipt", None)

_encoded = "".join(
    (_PART_00, _PART_01, _PART_02, _PART_03, _PART_04, _PART_05, _PART_06)
)
try:
    _source = _base64.b64decode(_encoded, validate=True)
except ValueError as exc:  # pragma: no cover - repository corruption guard
    raise ImportError("submission transport core source is not canonical base64") from exc
if len(_source) != 28619:
    raise ImportError("submission transport core source length mismatch")
if _hashlib.sha256(_source).hexdigest() != "ffaa12e964799dc173e9cd2b7dad47c73b91fc35aab1a05703c70527f91aec6d":
    raise ImportError("submission transport core source digest mismatch")

_payload_filename = f"{__file__}.payload.py"
_payload_text = _source.decode("utf-8")
_linecache.cache[_payload_filename] = (
    len(_source), None, _payload_text.splitlines(True), _payload_filename
)
_code = compile(_payload_text, _payload_filename, "exec")
exec(_code, globals(), globals())

del _base64, _hashlib, _linecache, _encoded, _source, _payload_text, _payload_filename, _code
del _PART_00, _PART_01, _PART_02, _PART_03, _PART_04, _PART_05, _PART_06
