from __future__ import annotations

import base64
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

if [len(part) for part in PARTS] != EXPECTED_LENGTHS:
    raise SystemExit(
        f"unexpected chunk lengths: {[len(part) for part in PARTS]!r}"
    )
if any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=" for part in PARTS for character in part):
    raise SystemExit("chunk contains non-base64-alphabet bytes")

unique: dict[str, dict[str, object]] = {}
second = PARTS[1]
for deletion_index, deleted_character in enumerate(second):
    encoded = (
        PARTS[0]
        + second[:deletion_index]
        + second[deletion_index + 1 :]
        + PARTS[2]
        + PARTS[3]
    )
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except Exception:
        continue
    if len(decoded) != 15_000:
        continue
    if not decoded.startswith(b"# SPDX-License-Identifier: MIT\n"):
        continue
    if not decoded.endswith(b"\n"):
        continue
    try:
        source = decoded.decode("utf-8")
        compile(source, "concierge/_revenue_settlement_core.py", "exec")
    except (UnicodeDecodeError, SyntaxError, ValueError):
        continue
    if any(marker not in source for marker in REQUIRED_MARKERS):
        continue
    digest = hashlib.sha256(decoded).hexdigest()
    candidate = unique.setdefault(
        digest,
        {"bytes": decoded, "deletions": []},
    )
    candidate["deletions"].append((deletion_index, deleted_character))

print(f"ZAQ_RECOVERY_UNIQUE_CANDIDATES {len(unique)}")
for digest, candidate in sorted(unique.items()):
    print(
        "ZAQ_RECOVERY_CANDIDATE",
        digest,
        candidate["deletions"],
    )

if len(unique) != 1:
    raise SystemExit("single-character recovery did not yield one unique source")

digest, candidate = next(iter(unique.items()))
recovered = candidate["bytes"]
target = Path("concierge/_revenue_settlement_core.py")
target.write_bytes(recovered)
print("ZAQ_RECOVERY_DELETIONS", candidate["deletions"])
print("ZAQ_CORE_BYTES", len(recovered))
print("ZAQ_CORE_SHA256", digest)
