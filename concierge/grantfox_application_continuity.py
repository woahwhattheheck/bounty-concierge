# SPDX-License-Identifier: MIT
"""Monotonic, advisory-only GrantFox application/assignment/payment ledger."""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from .grantfox_queue_gate import verify_receipt as verify_queue_receipt

SCHEMA = "grantfox-application-continuity/v1"
QUEUE_SCHEMA = "grantfox-queue-gate/v1"
KINDS = {
    "SNAPSHOT", "APPLICATION_RECEIPT", "ASSIGNMENT_RECEIPT",
    "SUBMISSION_RECEIPT", "ADJUDICATION_RECEIPT", "PAYMENT_RECEIPT",
}
AUTHORITY = {
    "advisory_only": True,
    "provider_application_authority": False,
    "implementation_write_authority": False,
    "submission_authority": False,
    "adjudication_authority": False,
    "payment_or_wallet_authority": False,
}
LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
SEG = r"[A-Za-z0-9_.-]+"
GH_ISSUE = re.compile(rf"^/({SEG})/({SEG})/issues/([1-9][0-9]*)/?$")
GH_PR = re.compile(rf"^/({SEG})/({SEG})/pull/([1-9][0-9]*)/?$")
GFOX = re.compile(rf"^/org/({SEG})/repo/({SEG})/issue/([1-9][0-9]*)/?$")
COMMENT = re.compile(r"^issuecomment-([1-9][0-9]*)$")
CURRENCY = re.compile(r"^[A-Z][A-Z0-9]{2,7}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_FIELDS = {"schema", "actor_login", "queue_receipt", "events"}
_RECEIPT_FIELDS = {
    "schema",
    "disposition",
    "state",
    "advisory_next_action",
    "reason_codes",
    "identity",
    "queue_anchor",
    "lifecycle",
    "events",
    "evidence",
    "authority",
    "receipt_sha256",
}
_EVIDENCE_FIELDS = {"queue_receipt", "actor_login", "events"}


class GrantFoxContinuityInputError(ValueError):
    pass


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _obj(v: Any, name: str) -> dict[str, Any]:
    if type(v) is not dict:
        raise GrantFoxContinuityInputError(f"{name} must be an object")
    return v


def _text(v: Any, name: str) -> str:
    if type(v) is not str or not v or v != v.strip():
        raise GrantFoxContinuityInputError(f"{name} must be a non-empty trimmed string")
    return v


def _login(v: Any, name: str, none: bool = False) -> str | None:
    if v is None and none:
        return None
    v = _text(v, name)
    if not LOGIN.fullmatch(v):
        raise GrantFoxContinuityInputError(f"{name} must be a GitHub-style login")
    return v.casefold()


def _time(v: Any, name: str) -> datetime:
    v = _text(v, name)
    if not v.endswith("Z"):
        raise GrantFoxContinuityInputError(f"{name} must be UTC RFC3339 ending in Z")
    try:
        dt = datetime.fromisoformat(v[:-1] + "+00:00")
    except ValueError as exc:
        raise GrantFoxContinuityInputError(f"{name} must be valid RFC3339") from exc
    if dt.utcoffset() is None or dt.utcoffset().total_seconds() != 0:
        raise GrantFoxContinuityInputError(f"{name} must be UTC")
    return dt


def _url(v: Any, name: str) -> tuple[str, str, str]:
    v = _text(v, name)
    if len(v) > 2048 or any(c.isspace() or ord(c) == 0x7F for c in v) or "\\" in v or "%" in v:
        raise GrantFoxContinuityInputError(f"{name} is not a strict URL")
    try:
        p, port = urlsplit(v), urlsplit(v).port
    except ValueError as exc:
        raise GrantFoxContinuityInputError(f"{name} is not a valid URL") from exc
    if p.scheme.casefold() != "https" or p.username or p.password or port is not None or p.query:
        raise GrantFoxContinuityInputError(f"{name} must be canonical https without query/userinfo/port")
    host = (p.hostname or "").casefold()
    if not host or p.path.startswith("//") or "//" in p.path:
        raise GrantFoxContinuityInputError(f"{name} has an invalid host/path")
    return host, p.path, p.fragment


def _identity(v: Any, name: str, kind: str) -> tuple[str, str, int]:
    host, path, fragment = _url(v, name)
    rx, hosts = (GFOX, {"contribute.grantfox.xyz"}) if kind == "gfox" else (
        GH_ISSUE if kind == "issue" else GH_PR,
        {"github.com", "www.github.com"},
    )
    if host not in hosts or fragment:
        raise GrantFoxContinuityInputError(f"{name} is not a canonical {kind} URL")
    m = rx.fullmatch(path)
    if not m:
        raise GrantFoxContinuityInputError(f"{name} is not a canonical {kind} URL")
    a, b, n = m.groups()
    return a.casefold(), b.casefold(), int(n)


def _comment(v: Any, name: str) -> tuple[str, str, int]:
    host, path, fragment = _url(v, name)
    m, c = GH_ISSUE.fullmatch(path), COMMENT.fullmatch(fragment)
    if host not in {"github.com", "www.github.com"} or not m or not c:
        raise GrantFoxContinuityInputError(f"{name} must be a GitHub issue-comment URL")
    a, b, n = m.groups()
    return a.casefold(), b.casefold(), int(n)


def _money(amount: Any, currency: Any, name: str) -> tuple[str, str]:
    a, c = _text(amount, f"{name}.amount"), _text(currency, f"{name}.currency")
    if not CURRENCY.fullmatch(c):
        raise GrantFoxContinuityInputError(f"{name}.currency is invalid")
    try:
        d = Decimal(a)
    except InvalidOperation as exc:
        raise GrantFoxContinuityInputError(f"{name}.amount must be decimal") from exc
    if not d.is_finite() or d <= 0 or d.as_tuple().exponent < -8:
        raise GrantFoxContinuityInputError(f"{name}.amount must be positive with <=8 decimals")
    return format(d, "f"), c


def _receipt_url(event: dict[str, Any], name: str, issue: tuple[str, str, int], listing: str) -> str:
    u = _text(event.get("receipt_url"), f"{name}.receipt_url")
    if u == listing:
        return u
    if _comment(u, f"{name}.receipt_url") != issue:
        raise GrantFoxContinuityInputError(f"{name}.receipt_url identifies a different issue")
    return u


def _queue_anchor(queue: Any) -> tuple[dict[str, Any], tuple[str, str, int], str]:
    q = _obj(queue, "queue_receipt")
    if q.get("schema") != QUEUE_SCHEMA:
        raise GrantFoxContinuityInputError("queue_receipt schema is invalid")
    if not verify_queue_receipt(q):
        raise GrantFoxContinuityInputError("queue_receipt digest does not verify")
    digest = q.get("receipt_sha256")
    if type(digest) is not str or not SHA256.fullmatch(digest):
        raise GrantFoxContinuityInputError("queue_receipt digest is invalid")
    ident = _obj(q.get("identity"), "queue_receipt.identity")
    g = _identity(ident.get("listing_url"), "queue_receipt.identity.listing_url", "gfox")
    i = _identity(ident.get("canonical_issue_url"), "queue_receipt.identity.canonical_issue_url", "issue")
    if g != i or (ident.get("owner"), ident.get("repo"), ident.get("issue_number")) != g:
        raise GrantFoxContinuityInputError("queue_receipt URLs identify a different issue")
    return ident, g, digest


def compile_continuity(request: dict[str, Any]) -> dict[str, Any]:
    r = _obj(request, "request")
    if r.get("schema") != SCHEMA:
        raise GrantFoxContinuityInputError(f"schema must equal {SCHEMA}")
    unknown_request = set(r) - _REQUEST_FIELDS
    if unknown_request:
        raise GrantFoxContinuityInputError(
            f"request contains unsupported fields: {sorted(unknown_request)}"
        )
    ident, issue, queue_digest = _queue_anchor(r.get("queue_receipt"))
    actor = _login(r.get("actor_login"), "actor_login")
    provider_snapshot = _obj(r["queue_receipt"].get("provider_snapshot"), "queue_receipt.provider_snapshot")
    queue_actor = provider_snapshot.get("actor_login")
    if queue_actor is not None and _login(queue_actor, "queue_receipt.provider_snapshot.actor_login") != actor:
        raise GrantFoxContinuityInputError("actor_login differs from queue receipt actor")
    events = r.get("events")
    if type(events) is not list:
        raise GrantFoxContinuityInputError("events must be a list")

    seen_ids: set[str] = set()
    durable_urls: set[str] = set()
    normalized: list[dict[str, Any]] = []
    reasons: list[str] = []
    last_time: datetime | None = None
    applied = assigned = False
    pr_url: str | None = None
    adjudication: str | None = None
    approved: tuple[str, str] | None = None
    payment_sent = payment_received = False
    listing = ident["listing_url"]

    for n, raw in enumerate(events):
        e = _obj(raw, f"events[{n}]")
        name = f"events[{n}]"
        event_id = _text(e.get("event_id"), f"{name}.event_id")
        if event_id in seen_ids:
            raise GrantFoxContinuityInputError("event_id values must be unique")
        seen_ids.add(event_id)
        kind = _text(e.get("kind"), f"{name}.kind").upper()
        if kind not in KINDS:
            raise GrantFoxContinuityInputError(f"{name}.kind is unsupported")
        observed = _time(e.get("observed_at"), f"{name}.observed_at")
        if last_time is not None and observed <= last_time:
            raise GrantFoxContinuityInputError("events must be strictly increasing by observed_at")
        last_time = observed
        out: dict[str, Any] = {"event_id": event_id, "kind": kind, "observed_at": e["observed_at"]}

        if kind == "SNAPSHOT":
            state = _text(e.get("issue_state"), f"{name}.issue_state").casefold()
            if state not in {"open", "closed"}:
                raise GrantFoxContinuityInputError(f"{name}.issue_state must be open or closed")
            snap_applied = e.get("actor_applied")
            if type(snap_applied) is not bool:
                raise GrantFoxContinuityInputError(f"{name}.actor_applied must be boolean")
            snap_assigned = _login(e.get("assigned_to"), f"{name}.assigned_to", none=True)
            links = e.get("linked_pr_urls", [])
            if type(links) is not list:
                raise GrantFoxContinuityInputError(f"{name}.linked_pr_urls must be a list")
            checked_links: list[str] = []
            for j, u in enumerate(links):
                u = _text(u, f"{name}.linked_pr_urls[{j}]")
                if _identity(u, f"{name}.linked_pr_urls[{j}]", "pr")[:2] != issue[:2]:
                    raise GrantFoxContinuityInputError("linked PR identifies a different repository")
                if u in checked_links:
                    raise GrantFoxContinuityInputError("linked_pr_urls must be unique")
                checked_links.append(u)
            if applied and not snap_applied:
                reasons.append("SNAPSHOT_APPLICATION_REGRESSION")
            if assigned and snap_assigned != actor:
                reasons.append("SNAPSHOT_ASSIGNMENT_REGRESSION")
            if snap_assigned not in {None, actor}:
                reasons.append("ASSIGNMENT_CONFLICT")
            if pr_url and pr_url not in checked_links:
                reasons.append("PR_CONFLICT")
            if pr_url is None and checked_links:
                reasons.append("LINKED_PR_WITHOUT_DURABLE_SUBMISSION_RECEIPT")
            if state == "closed" and adjudication is None:
                reasons.append("ISSUE_CLOSED_BEFORE_ADJUDICATION")
            out.update({"issue_state": state, "actor_applied": snap_applied, "assigned_to": snap_assigned, "linked_pr_urls": checked_links})

        elif kind in {"APPLICATION_RECEIPT", "ASSIGNMENT_RECEIPT"}:
            u = _receipt_url(e, name, issue, listing)
            if u in durable_urls:
                raise GrantFoxContinuityInputError("durable receipt URLs must be unique")
            durable_urls.add(u)
            if kind == "APPLICATION_RECEIPT":
                applied = True
                out["receipt_url"] = u
            else:
                if not applied:
                    raise GrantFoxContinuityInputError("ASSIGNMENT_RECEIPT requires prior application")
                who = _login(e.get("assigned_to"), f"{name}.assigned_to")
                if who != actor:
                    raise GrantFoxContinuityInputError("ASSIGNMENT_RECEIPT must assign configured actor")
                assigned = True
                out.update({"receipt_url": u, "assigned_to": who})

        elif kind == "SUBMISSION_RECEIPT":
            if not assigned:
                raise GrantFoxContinuityInputError("SUBMISSION_RECEIPT requires prior durable assignment")
            u = _text(e.get("pr_url"), f"{name}.pr_url")
            if _identity(u, f"{name}.pr_url", "pr")[:2] != issue[:2]:
                raise GrantFoxContinuityInputError("submission PR identifies a different repository")
            if pr_url and pr_url != u:
                raise GrantFoxContinuityInputError("conflicting submission PRs require reconciliation")
            pr_url = u
            out["pr_url"] = u

        elif kind == "ADJUDICATION_RECEIPT":
            if pr_url is None:
                raise GrantFoxContinuityInputError("ADJUDICATION_RECEIPT requires prior submission")
            if adjudication is not None:
                raise GrantFoxContinuityInputError("only one adjudication receipt is allowed")
            u = _receipt_url(e, name, issue, listing)
            if u in durable_urls:
                raise GrantFoxContinuityInputError("durable receipt URLs must be unique")
            durable_urls.add(u)
            adjudication = _text(e.get("outcome"), f"{name}.outcome").upper()
            if adjudication not in {"APPROVED", "REJECTED"}:
                raise GrantFoxContinuityInputError("adjudication outcome must be APPROVED or REJECTED")
            out.update({"receipt_url": u, "outcome": adjudication})
            if "amount" in e or "currency" in e:
                if adjudication != "APPROVED" or "amount" not in e or "currency" not in e:
                    raise GrantFoxContinuityInputError("money may appear only as a complete approved amount/currency pair")
                approved = _money(e["amount"], e["currency"], name)
                out.update({"amount": approved[0], "currency": approved[1]})

        else:
            if adjudication != "APPROVED":
                raise GrantFoxContinuityInputError("PAYMENT_RECEIPT requires APPROVED adjudication")
            status = _text(e.get("status"), f"{name}.status").upper()
            if status not in {"SENT", "RECEIVED"}:
                raise GrantFoxContinuityInputError("payment status must be SENT or RECEIVED")
            u = _text(e.get("receipt_url"), f"{name}.receipt_url")
            host, _, _ = _url(u, f"{name}.receipt_url")
            if host in {"localhost", "127.0.0.1"} or u in durable_urls:
                raise GrantFoxContinuityInputError("payment receipt URL must be unique and externally verifiable")
            durable_urls.add(u)
            money = _money(e.get("amount"), e.get("currency"), name)
            if approved and money != approved:
                reasons.append("PAYMENT_AMOUNT_DIFFERS_FROM_APPROVED")
            if status == "SENT" and payment_received:
                raise GrantFoxContinuityInputError("payment cannot regress from RECEIVED to SENT")
            payment_sent = True
            payment_received = payment_received or status == "RECEIVED"
            out.update({"status": status, "receipt_url": u, "amount": money[0], "currency": money[1], "receipt_ref": _text(e.get("receipt_ref"), f"{name}.receipt_ref")})

        normalized.append(out)

    reasons = list(dict.fromkeys(reasons))
    if payment_received:
        state, next_action = "PAID", "COMPLETE_PAID_AND_ARCHIVE_RECEIPTS"
    elif payment_sent:
        state, next_action = "PAYMENT_SENT", "VERIFY_PROVIDER_RECEIPT_AND_ACTUAL_RECEIPT"
    elif adjudication == "APPROVED":
        state, next_action = "APPROVED", "COLLECT_PAYMENT_THROUGH_PROVIDER_ROUTE"
    elif adjudication == "REJECTED":
        state, next_action = "REJECTED", "COMPLETE_REJECTED_OR_USE_DOCUMENTED_APPEAL_ROUTE"
    elif pr_url:
        state, next_action = "SUBMITTED", "WAIT_FOR_PROVIDER_ADJUDICATION"
    elif assigned:
        state, next_action = "ASSIGNED", "IMPLEMENT_ASSIGNED_SCOPE"
    elif applied:
        state, next_action = "APPLIED", "WAIT_FOR_PROVIDER_ASSIGNMENT"
    else:
        state, next_action = "DISCOVERED", "USE_FRESH_QUEUE_GATE_BEFORE_APPLYING"
    disposition = "HOLD_RECONCILE" if reasons else "CONTINUE"
    body = {
        "schema": SCHEMA,
        "disposition": disposition,
        "state": state,
        "advisory_next_action": "STOP_AND_RECONCILE_DURABLE_EVIDENCE" if reasons else next_action,
        "reason_codes": reasons,
        "identity": {"owner": issue[0], "repo": issue[1], "issue_number": issue[2], "listing_url": ident["listing_url"], "canonical_issue_url": ident["canonical_issue_url"], "actor_login": actor},
        "queue_anchor": {"schema": QUEUE_SCHEMA, "receipt_sha256": queue_digest},
        "lifecycle": {"applied": applied, "assigned": assigned, "submission_pr_url": pr_url, "adjudication": adjudication, "approved_amount": approved[0] if approved else None, "approved_currency": approved[1] if approved else None, "payment_sent": payment_sent, "payment_received": payment_received},
        "events": normalized,
        "evidence": {
            "queue_receipt": r["queue_receipt"],
            "actor_login": actor,
            "events": [dict(event) for event in events],
        },
        "authority": AUTHORITY,
    }
    return {**body, "receipt_sha256": _hash(body)}


def verify_continuity_receipt(receipt: dict[str, Any]) -> bool:
    """Recompile lifecycle semantics from retained queue, actor, and event evidence."""
    if type(receipt) is not dict or set(receipt) != _RECEIPT_FIELDS:
        return False
    if receipt.get("schema") != SCHEMA or receipt.get("authority") != AUTHORITY:
        return False
    digest = receipt.get("receipt_sha256")
    if type(digest) is not str or not SHA256.fullmatch(digest):
        return False

    evidence = receipt.get("evidence")
    if type(evidence) is not dict or set(evidence) != _EVIDENCE_FIELDS:
        return False
    queue = evidence.get("queue_receipt")
    actor = evidence.get("actor_login")
    events = evidence.get("events")
    if type(queue) is not dict or type(actor) is not str or type(events) is not list:
        return False

    try:
        expected = compile_continuity(
            {
                "schema": SCHEMA,
                "queue_receipt": queue,
                "actor_login": actor,
                "events": events,
            }
        )
    except (GrantFoxContinuityInputError, KeyError, TypeError, ValueError):
        return False
    return expected == receipt


def format_summary(receipt: dict[str, Any]) -> str:
    i = receipt["identity"]
    reasons = ",".join(receipt["reason_codes"]) or "none"
    return f"disposition={receipt['disposition']} state={receipt['state']} issue={i['owner']}/{i['repo']}#{i['issue_number']} next={receipt['advisory_next_action']} reasons={reasons} receipt_sha256={receipt['receipt_sha256']}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m concierge.grantfox_application_continuity")
    p.add_argument("ledger", help="ledger JSON path, or - for stdin")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        if a.ledger == "-":
            import sys
            payload = json.load(sys.stdin)
        else:
            with Path(a.ledger).open(encoding="utf-8") as f:
                payload = json.load(f)
        receipt = compile_continuity(payload)
    except (OSError, json.JSONDecodeError, GrantFoxContinuityInputError) as exc:
        p.error(str(exc))
    print(json.dumps(receipt, indent=2, sort_keys=True) if a.json else format_summary(receipt))
    return 2 if receipt["disposition"] == "HOLD_RECONCILE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
