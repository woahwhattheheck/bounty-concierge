from __future__ import annotations

import base64
import bisect
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PARTS = [
    (ROOT / f"settlement_core.b64.{index:02d}").read_text(encoding="ascii")
    for index in range(4)
]
EXPECTED_LENGTHS = [5000, 5001, 5000, 5000]
REQUIRED_MARKERS = (
    "class RevenueSettlementInputError",
    "def _history_capture(",
    "_MAX_ITEMS",
    "canonical history capture wallet metadata was invalid",
    "if __name__ == \"__main__\"",
)
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="

if [len(part) for part in PARTS] != EXPECTED_LENGTHS:
    raise SystemExit(
        f"unexpected chunk lengths: {[len(part) for part in PARTS]!r}"
    )
if any(character not in ALPHABET for part in PARTS for character in part):
    raise SystemExit("chunk contains non-base64-alphabet bytes")

encoded = "".join(PARTS)
boundaries: list[int] = []
running = 0
for part in PARTS:
    running += len(part)
    boundaries.append(running)

stages = {
    "decoded": 0,
    "length": 0,
    "prefix": 0,
    "terminal_newline": 0,
    "utf8": 0,
    "compiled": 0,
    "markers": 0,
}
compiled: dict[str, dict[str, object]] = {}
accepted: dict[str, dict[str, object]] = {}

for deletion_index, deleted_character in enumerate(encoded):
    candidate_encoded = encoded[:deletion_index] + encoded[deletion_index + 1 :]
    try:
        decoded = base64.b64decode(candidate_encoded, validate=True)
    except Exception:
        continue
    stages["decoded"] += 1
    if len(decoded) != 15_000:
        continue
    stages["length"] += 1
    if not decoded.startswith(b"# SPDX-License-Identifier: MIT\n"):
        continue
    stages["prefix"] += 1
    if decoded.endswith(b"\n"):
        stages["terminal_newline"] += 1
    try:
        source = decoded.decode("utf-8")
    except UnicodeDecodeError:
        continue
    stages["utf8"] += 1
    try:
        compile(source, "concierge/_revenue_settlement_core.py", "exec")
    except (SyntaxError, ValueError):
        continue
    stages["compiled"] += 1
    digest = hashlib.sha256(decoded).hexdigest()
    chunk_index = bisect.bisect_right(boundaries, deletion_index)
    chunk_start = 0 if chunk_index == 0 else boundaries[chunk_index - 1]
    deletion = (chunk_index, deletion_index - chunk_start, deleted_character)
    record = compiled.setdefault(
        digest,
        {"bytes": decoded, "deletions": [], "missing_markers": []},
    )
    record["deletions"].append(deletion)
    missing = [marker for marker in REQUIRED_MARKERS if marker not in source]
    record["missing_markers"] = missing
    if not missing:
        stages["markers"] += 1
        accepted[digest] = record

print("ZAQ_RECOVERY_STAGES", stages)
print("ZAQ_RECOVERY_COMPILED_UNIQUE", len(compiled))
for digest, record in sorted(compiled.items()):
    print(
        "ZAQ_RECOVERY_COMPILED",
        digest,
        record["deletions"],
        "missing_markers=",
        record["missing_markers"],
    )
print("ZAQ_RECOVERY_ACCEPTED_UNIQUE", len(accepted))

selection = compiled if len(compiled) == 1 else accepted
if len(selection) != 1:
    raise SystemExit("full-stream single-character recovery remained ambiguous")

digest, record = next(iter(selection.items()))
recovered = record["bytes"]
target = Path("concierge/_revenue_settlement_core.py")
target.write_bytes(recovered)
print("ZAQ_RECOVERY_DELETIONS", record["deletions"])
print("ZAQ_RECOVERY_MISSING_MARKERS", record["missing_markers"])
print("ZAQ_CORE_BYTES", len(recovered))
print("ZAQ_CORE_SHA256", digest)
