# SPDX-License-Identifier: MIT
"""Current-state Open Collective expense proof from a complete provider page.

Never turn an old "Expense paid" activity (or a search snippet) into present
payment evidence. The caller supplies text captured from the *complete*
canonical expense page and its actual UTC retrieval time. This is an offline
parser, not a network collector, authentication check, or payment receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

_SCHEMA = "opencollective-expense-proof/v1"
_EXPENSE_PATH = re.compile(r"^/([A-Za-z0-9][A-Za-z0-9_-]*)/expenses/([1-9][0-9]*)/?$")
_HEAD_INVOICE = re.compile(r"^#+\s*Invoice #([1-9][0-9]*)\b")
_INVOICE = re.compile(r"\bInvoice #([1-9][0-9]*)\b")
_DATE = re.compile(r"^on\s+([A-Z][a-z]+ [1-9][0-9]?, [0-9]{4})$")
_STATUS_ALIASES = {
    "approved": "APPROVED",
    "paid": "PAID",
    "pending": "PENDING",
    "unverified": "UNVERIFIED",
    "draft": "DRAFT",
    "incomplete": "INCOMPLETE",
    "on hold": "ON_HOLD",
    "rejected": "REJECTED",
    "processing": "PROCESSING",
    "error": "ERROR",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
    "scheduled for pay": "SCHEDULED_FOR_PAYMENT",
    "scheduled for payment": "SCHEDULED_FOR_PAYMENT",
    "ready to pay": "READY_TO_PAY",
}
_ACTIVITY_STATES = {
    "approved": "APPROVED",
    "paid": "PAID",
    "marked as incomplete": "INCOMPLETE",
    "processing": "PROCESSING",
    "error": "ERROR",
    "rejected": "REJECTED",
    "marked as on hold": "ON_HOLD",
    "scheduled for payment": "SCHEDULED_FOR_PAYMENT",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
    "pending": "PENDING",
}
_NON_STATES = {"invited", "created", "updated"}


class ExpenseProofError(ValueError):
    """Supplied page text cannot establish an unambiguous current status."""


def _expense_identity(url: str) -> tuple[str, int]:
    if type(url) is not str:
        raise ExpenseProofError("expense_url must be a canonical https URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ExpenseProofError("expense_url is malformed") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "opencollective.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
    ):
        raise ExpenseProofError("expense_url must be a canonical Open Collective detail URL")
    match = _EXPENSE_PATH.fullmatch(parsed.path)
    if not match:
        raise ExpenseProofError("expense_url path must identify one collective and expense")
    slug, number = match.groups()
    return slug, int(number)


def _observed_at(value: str) -> str:
    if type(value) is not str:
        raise ExpenseProofError("observed_at must be an explicit UTC ISO timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExpenseProofError("observed_at must be an ISO timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset().total_seconds() != 0:
        raise ExpenseProofError("observed_at must contain a UTC offset")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _activity_date(lines: list[str], activity_index: int, previous_index: int) -> str | None:
    """Use only the nearby activity byline; never invent a date for an error."""
    floor = max(previous_index + 1, activity_index - 5)
    for i in range(activity_index - 1, floor - 1, -1):
        match = _DATE.fullmatch(lines[i])
        if match:
            try:
                return datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()
            except ValueError as exc:
                raise ExpenseProofError("provider activity date was malformed") from exc
    return None


def assess_expense_page(page_text: str, *, expense_url: str, observed_at: str) -> dict:
    """Evaluate a full rendered provider detail page, fail closed on ambiguity.

    Input must be the complete line-oriented visible provider page, not a
    search-result preview, activity-only excerpt, or manually asserted status.
    Parsed activity dates are day-resolution; within-day order remains exactly
    the provider display order. A status after the latest dated event without
    its own date is not accepted as payment proof.
    """
    slug, number = _expense_identity(expense_url)
    timestamp = _observed_at(observed_at)
    if type(page_text) is not str or not 0 < len(page_text) <= 2_000_000:
        raise ExpenseProofError("page_text must be a nonempty bounded full-page capture")
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    heads = [(i, _HEAD_INVOICE.fullmatch(line)) for i, line in enumerate(lines)]
    heads = [(i, match) for i, match in heads if match]
    if not heads or any(int(match.group(1)) != number for _, match in heads):
        raise ExpenseProofError("invoice heading missing or does not match expense_url")
    markers = ["Expense Details", "More actions", "Collective balance", "Current Fiscal Host"]
    positions = []
    for marker in markers:
        choices = [i for i, line in enumerate(lines) if marker in line]
        if not choices:
            raise ExpenseProofError("incomplete provider page: " + marker + " missing")
        positions.append(choices[0])
    details, history_start, history_end, host = positions
    if not (heads[0][0] < details < history_start < history_end < host):
        raise ExpenseProofError("provider page sections are incomplete or out of order")
    for line in lines[:details]:
        match = _INVOICE.search(line)
        if match and int(match.group(1)) != number:
            raise ExpenseProofError("mixed invoice identifiers in page header")
    # Status is a standalone banner between the invoice heading and detail
    # section. Do not hunt the full page: past activity contains "Expense paid".
    banner = [(_STATUS_ALIASES[line.casefold()], i)
              for i, line in enumerate(lines[heads[0][0]+1:details], heads[0][0]+1)
              if line.casefold() in _STATUS_ALIASES]
    if len(banner) != 1:
        raise ExpenseProofError("current provider status banner is ambiguous")
    current_status = banner[0][0]
    events = []
    last_activity = history_start
    last_dated = ""
    for i in range(history_start + 1, history_end):
        line = lines[i]
        if not line.startswith("Expense "):
            continue
        action = line[len("Expense "):].casefold()
        if action not in _NON_STATES and action not in _ACTIVITY_STATES:
            raise ExpenseProofError("unrecognized expense activity; cannot certify history")
        date = _activity_date(lines, i, last_activity)
        if date is not None:
            if last_dated and date < last_dated:
                raise ExpenseProofError("provider activity chronology regressed")
            last_dated = date
        events.append({
            "sequence": len(events) + 1,
            "action": action,
            "status": _ACTIVITY_STATES.get(action),
            "date": date,
        })
        last_activity = i
    transitions = [event for event in events if event["status"] is not None]
    if not transitions:
        raise ExpenseProofError("provider page missing status activity history")
    latest = transitions[-1]
    if latest["status"] != current_status or latest["date"] is None:
        raise ExpenseProofError("current banner is not confirmed by latest dated activity")
    if latest["date"] > timestamp[:10]:
        raise ExpenseProofError("activity appears later than the capture time")
    paid_events = sum(event["status"] == "PAID" for event in transitions)
    return {
        "schema": _SCHEMA,
        "source_kind": "supplied_full_provider_detail_page",
        "expense_url": expense_url,
        "expense_id": number,
        "collective": slug,
        "observed_at": timestamp,
        "source_sha256": hashlib.sha256(page_text.encode("utf-8")).hexdigest(),
        "current_status": current_status,
        "paid_now_on_provider_page": current_status == "PAID",
        "historical_paid_events": paid_events,
        "latest_transition": latest,
        "activity_count": len(events),
        "activities": events,
        "note": "Page-state evidence only; neither claimant entitlement nor bank settlement is established.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate an Open Collective expense page")
    parser.add_argument("capture", type=Path, help="complete provider page text file")
    parser.add_argument("--expense-url", required=True)
    parser.add_argument("--observed-at", required=True, help="actual UTC capture timestamp")
    args = parser.parse_args(argv)
    try:
        report = assess_expense_page(
            args.capture.read_text(encoding="utf-8"),
            expense_url=args.expense_url,
            observed_at=args.observed_at,
        )
    except (OSError, UnicodeError, ExpenseProofError) as exc:
        parser.exit(2, "HOLD: " + str(exc) + "\n")
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
