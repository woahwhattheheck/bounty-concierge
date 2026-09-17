# SPDX-License-Identifier: MIT
"""Deterministic portfolio replay over paid-work effort/value gate receipts.

This module does not discover opportunities and does not perform live intake.
It consumes a bounded retained inventory, recompiles every #216 paid-work
value gate request, verifies the retained receipt, and partitions work by the
four gate decisions. Verified GO rows are only eligible for the existing #220
live dispatch seam; they are not claims, submissions, payout assertions, or
revenue.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Mapping, Sequence

from concierge.paid_work_effort_value_gate import (
    compile_paid_work_effort_value_gate,
    verify_receipt as verify_paid_work_receipt,
)


INPUT_SCHEMA = "paid-work-economics-portfolio-input/v1"
OUTPUT_SCHEMA = "paid-work-economics-portfolio/v1"
RECEIPT_SCHEMA = "paid-work-economics-portfolio-receipt/v1"
DECISIONS = (
    "GO",
    "HOLD_VALUE_UNKNOWN",
    "HOLD_ACCOUNT_GATE",
    "SKIP_ECONOMICS",
)
MAX_BYTES = 8 * 1024 * 1024
MAX_CANDIDATES = 500
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class PaidWorkPortfolioError(ValueError):
    """Raised when retained portfolio evidence is not safe to replay."""


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise PaidWorkPortfolioError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _reject_number(value: str) -> Any:
    raise PaidWorkPortfolioError(f"non-integer JSON number forbidden: {value}")


def loads_strict(text: str) -> dict[str, Any]:
    if type(text) is not str:
        raise PaidWorkPortfolioError("JSON input must be text")
    try:
        encoded = text.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise PaidWorkPortfolioError("invalid UTF-8") from exc
    if text.startswith("\ufeff") or len(encoded) > MAX_BYTES:
        raise PaidWorkPortfolioError("invalid or oversized JSON input")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except PaidWorkPortfolioError:
        raise
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise PaidWorkPortfolioError("invalid JSON") from exc
    if type(value) is not dict:
        raise PaidWorkPortfolioError("top-level JSON must be an object")
    return value


def canonical(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise PaidWorkPortfolioError("non-canonical value") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _keys(value: Any, expected: Sequence[str], where: str) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != set(expected):
        raise PaidWorkPortfolioError(f"{where}: keys mismatch")
    return value


def _ident(value: Any, where: str) -> str:
    if type(value) is not str or value != value.strip() or not ID_RE.fullmatch(value):
        raise PaidWorkPortfolioError(f"{where}: invalid identifier")
    return value


def _sha(value: Any, where: str) -> str:
    if type(value) is not str or not SHA_RE.fullmatch(value):
        raise PaidWorkPortfolioError(f"{where}: invalid sha256")
    return value


def _integer(value: Any, where: str, lo: int, hi: int) -> int:
    if type(value) is not int or not lo <= value <= hi:
        raise PaidWorkPortfolioError(f"{where}: invalid integer")
    return value


def _timestamp(value: Any, where: str) -> datetime:
    if type(value) is not str or TS_RE.fullmatch(value) is None:
        raise PaidWorkPortfolioError(f"{where}: exact UTC timestamp required")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise PaidWorkPortfolioError(f"{where}: invalid timestamp") from exc
    return parsed


def _authority() -> dict[str, bool]:
    return {
        "external_claim_authority": False,
        "external_submission_authority": False,
        "sponsor_contact_authority": False,
        "provider_mutation_authority": False,
        "payment_or_wallet_mutation_authority": False,
        "payout_assertion_authority": False,
        "accounting_revenue_authority": False,
        "cash_or_revenue_claimed": False,
    }


def _reason_worklist(reason_codes: Any) -> list[dict[str, str]]:
    if type(reason_codes) is not list or not all(type(code) is str for code in reason_codes):
        raise PaidWorkPortfolioError("gate reason_codes must be strings")
    rows: list[dict[str, str]] = []
    for code in sorted(set(reason_codes)):
        upper = code.upper()
        if any(token in upper for token in ("PAYOUT_ROUTE", "ACCOUNT_KYC", "ACCEPTANCE")):
            category = "account_or_acceptance"
        elif any(
            token in upper
            for token in (
                "MODEL_TOOL_COST",
                "NONCASH",
                "FX",
                "VALUE",
                "REWARD",
                "ECONOMIC",
                "POST_COST",
                "MARGIN",
            )
        ):
            category = "valuation_or_economics"
        elif "DEADLINE" in upper:
            category = "deadline"
        elif "CLAIM_CONGESTION" in upper:
            category = "congestion"
        elif any(token in upper for token in ("EVIDENCE", "STALE", "SOURCE", "FRESH")):
            category = "source_or_freshness"
        else:
            category = "other"
        rows.append({"category": category, "reason_code": code})
    return rows


def _normalize_inventory(raw: Any, as_of: datetime, max_age: int) -> dict[str, Any]:
    raw = _keys(
        raw,
        (
            "inventory_id",
            "generation_sha256",
            "captured_at",
            "complete_through",
            "complete",
        ),
        "inventory",
    )
    inventory_id = _ident(raw["inventory_id"], "inventory.inventory_id")
    generation = _sha(raw["generation_sha256"], "inventory.generation_sha256")
    captured = _timestamp(raw["captured_at"], "inventory.captured_at")
    complete_through = _timestamp(raw["complete_through"], "inventory.complete_through")
    if type(raw["complete"]) is not bool:
        raise PaidWorkPortfolioError("inventory.complete must be boolean")
    if not raw["complete"]:
        raise PaidWorkPortfolioError("inventory is incomplete")
    if captured > as_of or complete_through > captured:
        raise PaidWorkPortfolioError("inventory chronology invalid")
    captured_age = int((as_of - captured).total_seconds())
    coverage_age = int((as_of - complete_through).total_seconds())
    if captured_age > max_age or coverage_age > max_age:
        raise PaidWorkPortfolioError("inventory is stale")
    return {
        "inventory_id": inventory_id,
        "generation_sha256": generation,
        "captured_at": raw["captured_at"],
        "complete_through": raw["complete_through"],
        "complete": True,
        "captured_age_seconds": captured_age,
        "coverage_age_seconds": coverage_age,
    }


def _gate_identity(request: Mapping[str, Any], receipt: Mapping[str, Any]) -> tuple[str, str]:
    candidate = request.get("candidate")
    if type(candidate) is not dict:
        raise PaidWorkPortfolioError("gate request candidate is missing")
    work_id = _ident(candidate.get("work_id"), "gate_request.candidate.work_id")
    canonical_url = candidate.get("canonical_source_url")
    if type(canonical_url) is not str or not canonical_url or canonical_url != canonical_url.strip():
        raise PaidWorkPortfolioError("gate request canonical_source_url invalid")
    if receipt.get("work_id") != work_id:
        raise PaidWorkPortfolioError("gate receipt work_id mismatch")
    if receipt.get("canonical_source_url") != canonical_url:
        raise PaidWorkPortfolioError("gate receipt canonical source mismatch")
    return work_id, canonical_url


def _replay_item(
    raw: Any,
    portfolio_as_of: datetime,
    max_gate_age_seconds: int,
) -> dict[str, Any]:
    raw = _keys(
        raw,
        ("candidate_generation_id", "candidate_generation_sha256", "gate_request", "gate_receipt"),
        "candidate",
    )
    generation_id = _ident(raw["candidate_generation_id"], "candidate.candidate_generation_id")
    generation_sha = _sha(
        raw["candidate_generation_sha256"],
        "candidate.candidate_generation_sha256",
    )
    request = raw["gate_request"]
    receipt = raw["gate_receipt"]
    if type(request) is not dict or type(receipt) is not dict:
        raise PaidWorkPortfolioError("gate request and receipt must be objects")

    expected = compile_paid_work_effort_value_gate(copy.deepcopy(request))
    if type(expected) is not dict:
        raise PaidWorkPortfolioError("gate compiler did not return an object")
    if expected != receipt:
        raise PaidWorkPortfolioError("gate request/receipt replay mismatch")
    if not verify_paid_work_receipt(receipt):
        raise PaidWorkPortfolioError("gate receipt failed semantic verification")

    work_id, canonical_url = _gate_identity(request, receipt)
    request_as_of = _timestamp(request.get("as_of"), "gate_request.as_of")
    receipt_as_of = _timestamp(receipt.get("as_of"), "gate_receipt.as_of")
    if request_as_of != receipt_as_of:
        raise PaidWorkPortfolioError("gate request/receipt as_of mismatch")
    if receipt_as_of > portfolio_as_of:
        raise PaidWorkPortfolioError("gate receipt is from the future")
    gate_age = int((portfolio_as_of - receipt_as_of).total_seconds())
    if gate_age > max_gate_age_seconds:
        raise PaidWorkPortfolioError("gate receipt is stale for portfolio replay")

    decision = receipt.get("decision")
    if decision not in DECISIONS:
        raise PaidWorkPortfolioError("gate decision is unsupported")
    receipt_sha = _sha(receipt.get("receipt_sha256"), "gate_receipt.receipt_sha256")

    candidate_payload = request.get("candidate")
    if digest(candidate_payload) != generation_sha:
        raise PaidWorkPortfolioError("candidate generation digest mismatch")

    row = {
        "candidate_generation_id": generation_id,
        "candidate_generation_sha256": generation_sha,
        "work_id": work_id,
        "canonical_source_url": canonical_url,
        "decision": decision,
        "gate_as_of": receipt["as_of"],
        "gate_age_seconds": gate_age,
        "gate_receipt_sha256": receipt_sha,
        "reason_codes": sorted(set(receipt.get("reason_codes") or [])),
        "evidence_worklist": _reason_worklist(receipt.get("reason_codes") or []),
        "authority": _authority(),
    }
    if decision == "GO":
        row["implementation_dispatch_eligibility"] = {
            "eligible_for_existing_live_dispatch_revalidation": True,
            "downstream_seam": "concierge.revenue_dispatch.qualify_available_live_revenue_intake",
            "work_id": work_id,
            "gate_receipt_sha256": receipt_sha,
            "requires_fresh_live_intake_and_availability": True,
            "external_authority": False,
        }
    else:
        row["implementation_dispatch_eligibility"] = {
            "eligible_for_existing_live_dispatch_revalidation": False,
            "downstream_seam": "concierge.revenue_dispatch.qualify_available_live_revenue_intake",
            "work_id": work_id,
            "gate_receipt_sha256": receipt_sha,
            "requires_fresh_live_intake_and_availability": True,
            "external_authority": False,
        }
    return row


def compile_paid_work_economics_portfolio(raw: Mapping[str, Any]) -> dict[str, Any]:
    raw = _keys(raw, ("schema", "as_of", "policy", "inventory", "candidates"), "input")
    if raw["schema"] != INPUT_SCHEMA:
        raise PaidWorkPortfolioError("unsupported schema")
    as_of = _timestamp(raw["as_of"], "as_of")
    policy = _keys(
        raw["policy"],
        ("max_candidates", "max_inventory_age_seconds", "max_gate_age_seconds"),
        "policy",
    )
    max_candidates = _integer(policy["max_candidates"], "policy.max_candidates", 1, MAX_CANDIDATES)
    max_inventory_age = _integer(
        policy["max_inventory_age_seconds"],
        "policy.max_inventory_age_seconds",
        1,
        86400,
    )
    max_gate_age = _integer(
        policy["max_gate_age_seconds"],
        "policy.max_gate_age_seconds",
        1,
        86400,
    )
    inventory = _normalize_inventory(raw["inventory"], as_of, max_inventory_age)
    candidates = raw["candidates"]
    if type(candidates) is not list or not 1 <= len(candidates) <= max_candidates:
        raise PaidWorkPortfolioError("candidates must be a non-empty bounded list")

    rows = [_replay_item(item, as_of, max_gate_age) for item in candidates]
    generation_ids: set[str] = set()
    generation_shas: set[str] = set()
    work_ids: set[str] = set()
    canonical_urls: set[str] = set()
    for row in rows:
        gid = row["candidate_generation_id"]
        gsha = row["candidate_generation_sha256"]
        work_id = row["work_id"]
        url = row["canonical_source_url"]
        if gid in generation_ids or gsha in generation_shas:
            raise PaidWorkPortfolioError("duplicate or replayed candidate generation")
        if work_id in work_ids:
            raise PaidWorkPortfolioError("duplicate work_id alias")
        if url in canonical_urls:
            raise PaidWorkPortfolioError("duplicate canonical-source alias")
        generation_ids.add(gid)
        generation_shas.add(gsha)
        work_ids.add(work_id)
        canonical_urls.add(url)

    rows.sort(key=lambda row: (row["decision"], row["work_id"], row["candidate_generation_id"]))
    partitions = {
        decision: [copy.deepcopy(row) for row in rows if row["decision"] == decision]
        for decision in DECISIONS
    }
    implementation_queue = [
        {
            "work_id": row["work_id"],
            "canonical_source_url": row["canonical_source_url"],
            "candidate_generation_id": row["candidate_generation_id"],
            "candidate_generation_sha256": row["candidate_generation_sha256"],
            "gate_as_of": row["gate_as_of"],
            "gate_receipt_sha256": row["gate_receipt_sha256"],
            "next_seam": "concierge.revenue_dispatch.qualify_available_live_revenue_intake",
            "requires_fresh_live_intake_and_availability": True,
            "internal_implementation_only": True,
            "external_authority": False,
        }
        for row in partitions["GO"]
    ]

    report: dict[str, Any] = {
        "schema": OUTPUT_SCHEMA,
        "as_of": raw["as_of"],
        "input_sha256": digest(raw),
        "inventory": inventory,
        "policy": {
            "max_candidates": max_candidates,
            "max_inventory_age_seconds": max_inventory_age,
            "max_gate_age_seconds": max_gate_age,
        },
        "summary": {
            "candidate_count": len(rows),
            "go_count": len(partitions["GO"]),
            "hold_value_unknown_count": len(partitions["HOLD_VALUE_UNKNOWN"]),
            "hold_account_gate_count": len(partitions["HOLD_ACCOUNT_GATE"]),
            "skip_economics_count": len(partitions["SKIP_ECONOMICS"]),
        },
        "partitions": partitions,
        "implementation_queue": implementation_queue,
        "authority": _authority(),
        "interpretation": (
            "GO means eligible only for fresh revalidation at the existing #220 live dispatch seam; "
            "it is not a claim, submission, payout assertion, cash event, or revenue."
        ),
    }
    body = copy.deepcopy(report)
    report["report_sha256"] = digest(body)
    return report


def verify_paid_work_economics_portfolio(raw: Mapping[str, Any], report: Mapping[str, Any]) -> bool:
    if type(report) is not dict or report.get("schema") != OUTPUT_SCHEMA:
        raise PaidWorkPortfolioError("portfolio report schema mismatch")
    expected = compile_paid_work_economics_portfolio(copy.deepcopy(raw))
    if expected != report:
        raise PaidWorkPortfolioError("portfolio report replay mismatch")
    body = copy.deepcopy(report)
    claimed = body.pop("report_sha256", None)
    if claimed != digest(body):
        raise PaidWorkPortfolioError("portfolio report digest mismatch")
    if any(report.get("authority", {}).values()):
        raise PaidWorkPortfolioError("portfolio report authority ceiling violated")
    for row in report.get("implementation_queue", []):
        if row.get("external_authority") is not False:
            raise PaidWorkPortfolioError("implementation queue external authority violated")
    return True


def render_markdown(report: Mapping[str, Any]) -> str:
    if type(report) is not dict or report.get("schema") != OUTPUT_SCHEMA:
        raise PaidWorkPortfolioError("invalid report for rendering")
    summary = report["summary"]
    lines = [
        "# Paid-work economics portfolio replay",
        "",
        f"- As of: `{report['as_of']}`",
        f"- Inventory: `{report['inventory']['inventory_id']}`",
        f"- Candidates: `{summary['candidate_count']}`",
        f"- GO: `{summary['go_count']}`",
        f"- HOLD_VALUE_UNKNOWN: `{summary['hold_value_unknown_count']}`",
        f"- HOLD_ACCOUNT_GATE: `{summary['hold_account_gate_count']}`",
        f"- SKIP_ECONOMICS: `{summary['skip_economics_count']}`",
        "",
        "## GO implementation queue",
        "",
    ]
    if not report["implementation_queue"]:
        lines.append("- None")
    else:
        for row in report["implementation_queue"]:
            lines.append(
                f"- `{row['work_id']}` — {row['canonical_source_url']} — gate `{row['gate_receipt_sha256']}`"
            )
    for decision in DECISIONS[1:]:
        lines.extend(["", f"## {decision}", ""])
        rows = report["partitions"][decision]
        if not rows:
            lines.append("- None")
            continue
        for row in rows:
            reasons = ", ".join(row["reason_codes"]) or "none"
            lines.append(f"- `{row['work_id']}` — reasons: `{reasons}`")
            for item in row["evidence_worklist"]:
                lines.append(
                    f"  - `{item['category']}` → `{item['reason_code']}`"
                )
    lines.extend(
        [
            "",
            "## Authority ceiling",
            "",
            report["interpretation"],
            "",
            "All external claim/submission, sponsor-contact, provider/payment/wallet, payout, accounting, cash, and revenue authority remains false.",
            "",
            f"Report SHA-256: `{report['report_sha256']}`",
            "",
        ]
    )
    return "\n".join(lines)


def compile_bundle(raw: Mapping[str, Any]) -> tuple[bytes, bytes, bytes]:
    report = compile_paid_work_economics_portfolio(raw)
    report_bytes = canonical(report)
    markdown_bytes = render_markdown(report).encode("utf-8")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "input_sha256": digest(raw),
        "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "markdown_sha256": hashlib.sha256(markdown_bytes).hexdigest(),
        "go_work_ids": [row["work_id"] for row in report["implementation_queue"]],
        "authority": _authority(),
    }
    return report_bytes, markdown_bytes, canonical(receipt)


def verify_bundle(
    raw: Mapping[str, Any],
    report_bytes: bytes,
    markdown_bytes: bytes,
    receipt_bytes: bytes,
) -> bool:
    expected = compile_bundle(raw)
    if expected != (report_bytes, markdown_bytes, receipt_bytes):
        raise PaidWorkPortfolioError("portfolio bundle mismatch")
    return True


def _read_regular(path: str) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise PaidWorkPortfolioError(f"cannot open input: {exc}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
            raise PaidWorkPortfolioError("input must be a bounded regular file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_BYTES:
                raise PaidWorkPortfolioError("input exceeds size bound")
        after = os.fstat(fd)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise PaidWorkPortfolioError("input changed during read")
        data = b"".join(chunks)
        if len(data) != before.st_size:
            raise PaidWorkPortfolioError("partial input read")
        return data
    finally:
        os.close(fd)


def _read_json(path: str) -> dict[str, Any]:
    try:
        return loads_strict(_read_regular(path).decode("utf-8", "strict"))
    except UnicodeDecodeError as exc:
        raise PaidWorkPortfolioError("input must be strict UTF-8") from exc


def _write_exclusive(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(data)
    except FileExistsError as exc:
        raise PaidWorkPortfolioError(f"refusing to overwrite: {path}") from exc


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    compile_parser = sub.add_parser("compile")
    compile_parser.add_argument("input")
    compile_parser.add_argument("--out-dir", required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("input")
    verify_parser.add_argument("--report", required=True)
    verify_parser.add_argument("--markdown", required=True)
    verify_parser.add_argument("--receipt", required=True)
    args = parser.parse_args(argv)

    try:
        raw = _read_json(args.input)
        if args.command == "compile":
            report, markdown, receipt = compile_bundle(raw)
            out = Path(args.out_dir)
            if out.exists() or out.is_symlink():
                raise PaidWorkPortfolioError("output directory already exists")
            try:
                out.mkdir(parents=True)
            except FileExistsError as exc:
                raise PaidWorkPortfolioError("output directory already exists") from exc
            _write_exclusive(out / "portfolio.json", report)
            _write_exclusive(out / "portfolio.md", markdown)
            _write_exclusive(out / "receipt.json", receipt)
            print(f"COMPILED {hashlib.sha256(report).hexdigest()}")
            return 0
        report = _read_regular(args.report)
        markdown = _read_regular(args.markdown)
        receipt = _read_regular(args.receipt)
        verify_bundle(raw, report, markdown, receipt)
        print("VERIFIED")
        return 0
    except (PaidWorkPortfolioError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
