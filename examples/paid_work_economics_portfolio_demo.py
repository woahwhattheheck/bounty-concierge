"""Build a four-way synthetic paid-work economics portfolio replay."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from concierge.paid_work_effort_value_gate import compile_paid_work_effort_value_gate
from concierge.paid_work_economics_portfolio import compile_bundle, digest


POLICY = {
    "schema": "paid-work-effort-value-policy/v1",
    "evidence_max_age_seconds": 86400,
    "deadline_safety_seconds": 3600,
    "max_active_claims": 1,
    "fleet_economic_policy": {
        "schema": "fleet-economic-policy/v1",
        "min_batch_items": 2,
        "max_batch_items": 200,
        "currencies": {
            "USD": {
                "min_single_reward": "100",
                "min_batch_reward": "500",
                "min_reward_per_agent_hour": "100",
            }
        },
    },
}


def _evidence(state: str, tag: str, *, authority: str | None = None) -> dict:
    value = {
        "state": state,
        "evidence_url": f"https://synthetic.invalid/{tag}",
        "observed_at": "2026-09-16T19:30:00Z",
    }
    if authority is not None:
        value["authority"] = authority
    return value


def _candidate(work_id: str, number: int) -> dict:
    return {
        "work_id": work_id,
        "canonical_source_url": f"https://github.com/synthetic/repo/issues/{number}",
        "advertised_payout": {
            "amount": "300",
            "currency": "USD",
            "unit_type": "CASH",
            "observed_at": "2026-09-16T19:30:00Z",
        },
        "estimated_engineering_hours": "1",
        "model_tool_cost": {"state": "KNOWN", "amount": "10", "currency": "USD"},
        "deadline_at": "2026-09-17T20:00:00Z",
        "congestion": {"active_claims": 0, "observed_at": "2026-09-16T19:30:00Z"},
        "acceptance": _evidence("CONFIRMED", f"{work_id}-acceptance", authority="FIRST_PARTY"),
        "payout_route": _evidence("CONFIRMED", f"{work_id}-payout"),
        "account_kyc": _evidence("READY", f"{work_id}-kyc"),
    }


def _gate_item(work_id: str, number: int, decision_shape: str) -> dict:
    candidate = _candidate(work_id, number)
    if decision_shape == "hold-value":
        candidate["model_tool_cost"] = {"state": "UNKNOWN"}
    elif decision_shape == "hold-account":
        candidate["payout_route"] = _evidence("UNKNOWN", f"{work_id}-payout")
    elif decision_shape == "skip":
        candidate["advertised_payout"]["amount"] = "1"
        candidate["model_tool_cost"] = {"state": "KNOWN", "amount": "0", "currency": "USD"}
    request = {
        "schema": "paid-work-effort-value-gate/v1",
        "as_of": "2026-09-16T20:00:00Z",
        "policy": copy.deepcopy(POLICY),
        "candidate": candidate,
    }
    receipt = compile_paid_work_effort_value_gate(request)
    return {
        "candidate_generation_id": f"generation-{work_id}",
        "candidate_generation_sha256": digest(candidate),
        "gate_request": request,
        "gate_receipt": receipt,
    }


def demo_input() -> dict:
    return {
        "schema": "paid-work-economics-portfolio-input/v1",
        "as_of": "2026-09-16T20:10:00Z",
        "policy": {
            "max_candidates": 20,
            "max_inventory_age_seconds": 3600,
            "max_gate_age_seconds": 3600,
        },
        "inventory": {
            "inventory_id": "synthetic-paid-work-census",
            "generation_sha256": "a" * 64,
            "captured_at": "2026-09-16T20:05:00Z",
            "complete_through": "2026-09-16T20:04:00Z",
            "complete": True,
        },
        "candidates": [
            _gate_item("demo-go", 1, "go"),
            _gate_item("demo-value", 2, "hold-value"),
            _gate_item("demo-account", 3, "hold-account"),
            _gate_item("demo-skip", 4, "skip"),
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.out_dir.exists() or args.out_dir.is_symlink():
        raise SystemExit(f"refusing to overwrite existing output: {args.out_dir}")
    raw = demo_input()
    report, markdown, receipt = compile_bundle(raw)
    args.out_dir.mkdir(parents=True)
    (args.out_dir / "input.json").write_text(
        json.dumps(raw, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    (args.out_dir / "portfolio.json").write_bytes(report)
    (args.out_dir / "portfolio.md").write_bytes(markdown)
    (args.out_dir / "receipt.json").write_bytes(receipt)
    parsed = json.loads(report)
    print(f"PORTFOLIO_READY {parsed['report_sha256']}")
    print(json.dumps(parsed["summary"], sort_keys=True))
    print("GO remains internal eligibility only; #220 must revalidate live intake and availability.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
