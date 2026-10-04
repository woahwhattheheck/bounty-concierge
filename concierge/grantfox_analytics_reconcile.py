# SPDX-License-Identifier: MIT
"""Reconcile a captured GrantFox Analytics dashboard without network requests.

Public rows identify released milestones and GitHub handles, but omit PR URLs.
This report preserves that boundary and does not write to a settlement ledger.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


_HANDLE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+\Z")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"non-finite JSON number: {value}")


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty text")
    return value


def _count(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _handle(value):
    value = _text(value, "GitHub handle").strip()
    if not _HANDLE.fullmatch(value):
        raise ValueError(f"invalid GitHub handle: {value}")
    return value.casefold()


def _pr_url(value):
    parsed = urlsplit(value.strip())
    parts = parsed.path.rstrip("/").split("/")
    if (
        parsed.scheme != "https" or parsed.netloc.casefold() != "github.com"
        or len(parts) != 5 or parts[0] or parts[3] != "pull"
        or not _HANDLE.fullmatch(parts[1]) or not _REPO.fullmatch(parts[2])
        or not re.fullmatch(r"[1-9][0-9]*", parts[4])
    ):
        raise ValueError(f"expected a GitHub PR URL: {value}")
    owner, repo = parts[1].casefold(), parts[2].casefold()
    return f"https://github.com/{owner}/{repo}/pull/{parts[4]}", owner


def _amount(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValueError(f"{field} must be a JSON number")
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{field} must be finite and nonnegative")
    return amount


def _money(value):
    return format(value, "f")


def _subtotal(rows):
    amounts = [row["amount"] for row in rows]
    if not amounts:
        return Decimal(0)
    integer_digits = max(max(1, amount.adjusted() + 1) for amount in amounts)
    fraction_digits = max(max(0, -amount.as_tuple().exponent) for amount in amounts)
    with localcontext() as context:
        context.prec = max(28, integer_digits + fraction_digits + len(str(len(amounts))) + 1)
        return sum(amounts, Decimal(0))


def reconcile(document, claimants, pr_urls):
    """Return observations from the supported public dashboard JSON shape."""
    if not isinstance(document, dict):
        raise ValueError("snapshot must be a JSON object")
    payments = document.get("payments")
    summaries = document.get("summaries")
    summary = summaries.get("all") if isinstance(summaries, dict) else None
    if not isinstance(payments, dict) or not isinstance(summary, dict):
        raise ValueError("snapshot requires payments and summaries.all objects")
    rows = payments.get("items")
    if not isinstance(rows, list):
        raise ValueError("payments.items must be an array")
    total = _count(payments.get("total_count"), "payments.total_count")
    summary_count = _count(summary.get("contributor_payments"), "summaries.all.contributor_payments")
    if total < len(rows) or summary_count != total:
        raise ValueError("payment row count and summary count are inconsistent")
    truncated = payments.get("truncated")
    if not isinstance(truncated, bool) or truncated != (len(rows) < total):
        raise ValueError("payments.truncated is inconsistent with row counts")
    currency = _text(summary.get("currency"), "summaries.all.currency")
    snapshot_at = _text(summary.get("snapshot_at"), "summaries.all.snapshot_at")
    stamp = datetime.fromisoformat(snapshot_at.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("snapshot_at requires a timezone")

    handles = sorted({_handle(value) for value in claimants})
    known = dict(sorted(_pr_url(value) for value in pr_urls))
    if not handles or not known:
        raise ValueError("at least one claimant and one known PR URL are required")

    unique = {}
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"payments.items[{index}] must be an object")
        row = {
            field: _text(raw.get(field), f"payments.items[{index}].{field}")
            for field in (
                "payment_id", "project_id", "project_name", "campaign_id",
                "campaign_name", "escrow_id",
            )
        }
        username = raw.get("username")
        row["username"] = None if username is None else _handle(username)
        row["amount"] = _amount(raw.get("amount"), f"payments.items[{index}].amount")
        previous = unique.get(row["payment_id"])
        if previous is not None and previous != row:
            raise ValueError(f"conflicting payment_id: {row['payment_id']}")
        unique[row["payment_id"]] = row

    accepted = list(unique.values())
    matched = [row for row in accepted if row["username"] in handles]
    unknown = [row for row in accepted if row["username"] is None]
    candidate_ids = {url: [] for url in known}
    candidates = []
    for row in matched + unknown:
        possible = [
            url for url, owner in known.items()
            if row["project_name"].casefold() == owner
        ]
        if possible:
            candidates.append({
                "payment": row,
                "candidate_pr_urls": possible,
                "reason": "project_label_matches_owner_but_pr_link_missing",
                "claimant_resolved": row["username"] is not None,
            })
            for url in possible:
                candidate_ids[url].append(row["payment_id"])

    payer_history = []
    for owner in sorted(set(known.values())):
        group = [row for row in accepted if row["project_name"].casefold() == owner]
        payer_history.append({
            "repository_owner_label": owner,
            "association": "exact_project_label_only",
            "reported_released_subtotal": _money(_subtotal(group)),
            "payment_count": len(group),
            "claimant_match_count": sum(r["username"] in handles for r in group),
            "unresolved_username_count": sum(r["username"] is None for r in group),
            "payment_ids": [r["payment_id"] for r in group],
        })

    return {
        "schema": "grantfox-analytics-reconciliation/v1",
        "snapshot_at": snapshot_at,
        "currency": currency,
        "coverage": {
            "captured_row_count": len(rows),
            "unique_payment_count": len(accepted),
            "duplicate_row_count": len(rows) - len(accepted),
            "reported_total_count": total,
            "omitted_row_count": total - len(rows),
            "truncated": truncated,
            "unresolved_username_count": len(unknown),
            "complete_lifetime_payment_history": False,
        },
        "claimants": handles,
        "claimant_released_matches": matched,
        "observed_released_subtotal": _money(_subtotal(matched)),
        "observed_released_by_claimant": {
            handle: _money(_subtotal(r for r in matched if r["username"] == handle))
            for handle in handles
        },
        "unresolved_candidates": candidates,
        "unknown_username_payments": unknown,
        "unmatched_pr_records": [
            {"pr_url": url, "reason": "snapshot_has_no_pr_reference",
             "candidate_payment_ids": candidate_ids[url]}
            for url in known
        ],
        "payer_history": payer_history,
        "interpretation": [
            "Amounts are official dashboard observations, not an independent chain or account receipt.",
            "Zero matching rows is not proof of no lifetime payment.",
            "Known PR URLs are operator supplied; this command does not verify their authorship.",
            "Project label or claimant matches do not establish payment to a particular PR.",
            "Candidate amounts are not allocated across PRs or added to claimant subtotals.",
            "Payer-history subtotals include other contributors and are not our earnings.",
            "Snapshot time is not payment time; omitted rows and unresolved handles remain unknown.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True, help="Captured public dashboard JSON")
    parser.add_argument("--claimant", action="append", required=True, help="Exact GitHub handle; repeat as needed")
    parser.add_argument("--pr-url", action="append", required=True, help="Known contribution PR URL; repeat as needed")
    parser.add_argument("--output", type=Path, help="Create this file exclusively; default: JSON to stdout")
    args = parser.parse_args(argv)
    try:
        raw = args.snapshot.read_bytes()
        document = json.loads(raw, parse_float=Decimal, parse_constant=_constant, object_pairs_hook=_object)
        report = reconcile(document, args.claimant, args.pr_url)
        report["input_sha256"] = hashlib.sha256(raw).hexdigest()
        payload = json.dumps(report, ensure_ascii=False, indent=2, default=_money) + "\n"
        if args.output is None:
            sys.stdout.write(payload)
        else:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(payload)
    except (OSError, ValueError, TypeError, InvalidOperation) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
