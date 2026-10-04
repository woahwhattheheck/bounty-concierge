"""Offline reward-scan regression and timing against the immutable old source."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from concierge import bounty_index, reward_evidence

BASE = "4ec4bee4b75fc1db313e183b343457a536286144"


def baseline_module(path):
    source = subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT)
    module = types.ModuleType("baseline_" + Path(path).stem)
    exec(compile(source, path, "exec"), module.__dict__)
    return module


def spans(matches):
    return [(match.span(), match.group()) for match in matches]


def median_seconds(function, text, repeats=3):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        result = function("", text)
        samples.append(time.perf_counter() - start)
    return statistics.median(samples), result


def median_rows(function, rows):
    samples = []
    for _ in range(3):
        start = time.perf_counter()
        result = [function(row.get("title", ""), row.get("body", "")) for row in rows]
        samples.append(time.perf_counter() - start)
    return statistics.median(samples), result


def main():
    old = baseline_module("concierge/reward_evidence.py")
    old_index = baseline_module("concierge/bounty_index.py")
    index_path = ROOT / "data/bounty_index.json"
    rows = json.loads(index_path.read_bytes())["bounties"]
    old_rows = [old.extract_reward_evidence(row.get("title", ""), row.get("body", "")) for row in rows]
    new_rows = [reward_evidence.extract_reward_evidence(row.get("title", ""), row.get("body", "")) for row in rows]
    assert old_rows == new_rows, "retained evidence changed"
    batch_before, expected_rows = median_rows(old.extract_reward_evidence, rows)
    batch_after, actual_rows = median_rows(reward_evidence.extract_reward_evidence, rows)
    assert expected_rows == actual_rows
    for row in rows:
        args = row.get("title", ""), row.get("body", "")
        assert old_index.parse_reward(*args) == bounty_index.parse_reward(*args)

    # Prefix matches may consume only part of a malformed grouped number;
    # the subsequent currency match must begin after that earlier match.
    fixtures = [
        "", "100 USD", "x100USD", "1,000.50 USDC", "12.34 ETH",
        "1234,567 USD", "$1234,567 USD", "reward:1234,567USD",
        "123,4,567 USD", "123,4567,890 USD", "12,345000 USD",
        "12,0000.500 USD", "99.1.23 USD", "123USD456USD",
        "bounty100USD", "bounty100 USD", "1\nUSD", "١٢٣.٤٥ USD",
        "reward\t : \n of 42", "reward\t = \n of 42 USD",
        "reward of123", "rewardof123", "$1 reward:2 3USD",
        "50 RTC and $20 or 1,234.50 USD", "SATS 3 SATS_ 4 SATS",
    ]
    fixtures += [f"1 {currency}" for currency in (
        "USD", "USDT", "USDC", "EUR", "GBP", "SOL", "ETH", "SATS", "POINTS", "CREDITS"
    )]
    fixtures += [f"{prefix}{amount}{suffix}" for prefix in ("$", "reward: ", "x")
                 for amount in ("1234,567", "1,000.50", "١٢٣.٤٥")
                 for suffix in (" USD", "USDC", "\nETH")]
    for text in fixtures:
        assert spans(old._GENERAL_AMOUNT_PATTERN.finditer(text)) == spans(reward_evidence._iter_general_amounts(text)), text
        assert old.extract_reward_evidence("", text) == reward_evidence.extract_reward_evidence("", text), text
    for text in ("123 RTC", "١٢٣.٤٥ RTC", "१२३ RTC", "1,234.50 RTC"):
        assert old_index.parse_reward("", text) == bounty_index.parse_reward("", text), text
        assert old.extract_reward_evidence("", text) == reward_evidence.extract_reward_evidence("", text), text
    # ASCII already rejects an attached number; Unicode must not leak a suffix.
    for text in ("x123 RTC", "x١٢٣ RTC", "١٢٣٤,567 RTC"):
        assert bounty_index.parse_reward("", text) == 0.0, text
        assert reward_evidence.extract_reward_evidence("", text)["status"] == "no_match", text

    families = {
        "digits": lambda n: "1" * n,
        "comma_chain": lambda n: "1" + ",111" * (n // 4),
        "decimal": lambda n: "1." + "1" * n,
        "reward_whitespace": lambda n: "reward" + " " * n + "x",
        "unicode_digits": lambda n: "١" * n,
        "broken_group_before_currency": lambda n: "1" + ",111" * (n // 4) + ",1 USD",
    }
    measurements = []
    for name, make_text in families.items():
        for size in (512, 1024, 2048, 4096):
            text = make_text(size)
            before, expected = median_seconds(old.extract_reward_evidence, text)
            after, actual = median_seconds(reward_evidence.extract_reward_evidence, text)
            assert expected == actual, (name, size)
            measurements.append({"family": name, "characters": len(text),
                                 "before_seconds": before, "after_seconds": after,
                                 "speedup": before / after})
    scaling = []
    for name, make_text in families.items():
        for size in (8192, 16384, 32768, 65536):
            text = make_text(size)
            elapsed, _ = median_seconds(reward_evidence.extract_reward_evidence, text)
            scaling.append({"family": name, "characters": len(text), "after_seconds": elapsed})
    rtc_scaling = []
    for size in (1024, 2048, 4096):
        text = "١" * size
        before, expected = median_seconds(old_index.parse_reward, text)
        after, actual = median_seconds(bounty_index.parse_reward, text)
        assert expected == actual == 0.0
        rtc_scaling.append({"characters": size, "before_seconds": before, "after_seconds": after})

    result = {
        "baseline_commit": BASE,
        "python": sys.version,
        "repeats": 3,
        "timing_scope": "offline elapsed parser runtime; no provider calls or fleet-load claim",
        "retained_rows": len(rows),
        "retained_index_sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(),
        "retained_evidence_and_legacy_reward_parity": True,
        "retained_batch_timing": {"before_seconds": batch_before, "after_seconds": batch_after,
                                  "speedup": batch_before / batch_after},
        "general_match_span_and_order_fixtures": len(fixtures),
        "unicode_rtc_boundary_regressions": 3,
        "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                          for path in ("concierge/reward_evidence.py", "concierge/bounty_index.py")},
        "measurements": measurements,
        "patched_scaling": scaling,
        "legacy_rtc_scaling": rtc_scaling,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
