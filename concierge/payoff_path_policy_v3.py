# SPDX-License-Identifier: MIT
"""Evidence-bound payoff continuity v3 with explicit owner-policy supersession.

This module is an additive semantic extension for :mod:`payoff_path_gate_core`.
It keeps the v2 append-only receipt/root chain intact for existing documents while
adding a v3 document surface whose effort facts are evidence-bound and whose unpaid
budget authority is a versioned policy chain rather than a mutable scalar.

Importing :mod:`concierge` installs a small dispatcher into the landed core so the
existing descriptor-bound CLI wrapper can route v3 documents without duplicating its
filesystem custody code. Legacy v1 input remains fail-closed on every normal
library/CLI path; historical READY replay is available only through the explicitly
named migration helpers below.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import payoff_path_gate_core as _core

WORK_SCHEMA = "payoff-path-work/v3"
CONTINUITY_SCHEMA = "payoff-path-continuity/v2"
PACKET_SCHEMA = "payoff-path-gate/v3"
RECEIPT_SCHEMA = "payoff-path-gate-receipt/v3"
EVENT_KINDS = {"BUDGET_POLICY", "EFFORT"}
MODE = "CHAINED_POLICY_EVIDENCE"
_MAX_EVENTS = getattr(_core, "_MAX_EVENTS", 100_000)

_ORIGINAL_INTERNAL_COMPILE = _core._compile_gate
_ORIGINAL_VERIFY = _core.verify_gate
_INSTALLED = False


def _event_digest(event: dict[str, Any]) -> str:
    return _core._digest({"schema": CONTINUITY_SCHEMA, "event": event})


def policy_event_sha256(event: dict[str, Any]) -> str:
    """Return the canonical digest used by a successor policy predecessor pointer."""
    normalized = _normalize_event(event, "policy_event")
    if normalized["kind"] != "BUDGET_POLICY":
        raise _core.PayoffPathError("policy_event must have kind BUDGET_POLICY")
    return _event_digest(normalized)


def _normalize_event(raw: Any, name: str) -> dict[str, Any]:
    event = _core._exact_keys(
        raw,
        {
            "event_id", "kind", "opportunity_id", "work_id", "minutes",
            "occurred_at_utc", "evidence_ref", "evidence_sha256",
            "policy_generation", "predecessor_policy_sha256",
        },
        name,
    )
    event_id = _core._opaque_ref(event["event_id"], f"{name}.event_id")
    kind = _core._text(event["kind"], f"{name}.kind")
    if kind not in EVENT_KINDS:
        raise _core.PayoffPathError(f"{name}.kind is unsupported")
    opportunity_id = _core._opaque_ref(event["opportunity_id"], f"{name}.opportunity_id")
    minutes = _core._int(
        event["minutes"], f"{name}.minutes", minimum=1, maximum=_core._MAX_FREE_MINUTES
    )
    occurred_at_utc = _core._render_timestamp(
        _core._timestamp(event["occurred_at_utc"], f"{name}.occurred_at_utc")
    )
    evidence_ref = _core._opaque_ref(event["evidence_ref"], f"{name}.evidence_ref")
    evidence_sha256 = _core._sha(event["evidence_sha256"], f"{name}.evidence_sha256")
    if kind == "BUDGET_POLICY":
        if event["work_id"] is not None:
            raise _core.PayoffPathError(f"{name}.work_id must be null for BUDGET_POLICY")
        work_id = None
        policy_generation = _core._int(
            event["policy_generation"], f"{name}.policy_generation", minimum=0
        )
        predecessor = event["predecessor_policy_sha256"]
        if policy_generation == 0:
            if predecessor is not None:
                raise _core.PayoffPathError(
                    f"{name}.predecessor_policy_sha256 must be null for policy generation 0"
                )
            predecessor = None
        else:
            predecessor = _core._sha(predecessor, f"{name}.predecessor_policy_sha256")
    else:
        work_id = _core._opaque_ref(event["work_id"], f"{name}.work_id")
        if event["policy_generation"] is not None:
            raise _core.PayoffPathError(f"{name}.policy_generation must be null for EFFORT")
        if event["predecessor_policy_sha256"] is not None:
            raise _core.PayoffPathError(
                f"{name}.predecessor_policy_sha256 must be null for EFFORT"
            )
        policy_generation = None
        predecessor = None
    return {
        "event_id": event_id,
        "kind": kind,
        "opportunity_id": opportunity_id,
        "work_id": work_id,
        "minutes": minutes,
        "occurred_at_utc": occurred_at_utc,
        "evidence_ref": evidence_ref,
        "evidence_sha256": evidence_sha256,
        "policy_generation": policy_generation,
        "predecessor_policy_sha256": predecessor,
    }


def _event_sort_key(event: dict[str, Any]) -> tuple[str, int, str]:
    priority = 0 if event["kind"] == "BUDGET_POLICY" else 1
    return (event["occurred_at_utc"], priority, event["event_id"])


def _validate_policy_chains(events: list[dict[str, Any]]) -> None:
    by_opp: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event["kind"] == "BUDGET_POLICY":
            by_opp.setdefault(event["opportunity_id"], []).append(event)
    for opportunity_id, policies in by_opp.items():
        policies.sort(key=lambda row: row["policy_generation"])
        for expected_generation, policy in enumerate(policies):
            if policy["policy_generation"] != expected_generation:
                raise _core.PayoffPathError(
                    f"owner policy generations must be contiguous from 0 for opportunity: {opportunity_id}"
                )
            if expected_generation == 0:
                if policy["predecessor_policy_sha256"] is not None:
                    raise _core.PayoffPathError(
                        f"owner policy generation 0 has a predecessor for opportunity: {opportunity_id}"
                    )
                continue
            previous = policies[expected_generation - 1]
            if policy["occurred_at_utc"] < previous["occurred_at_utc"]:
                raise _core.PayoffPathError(
                    f"owner policy chronology regressed for opportunity: {opportunity_id}"
                )
            if policy["predecessor_policy_sha256"] != _event_digest(previous):
                raise _core.PayoffPathError(
                    f"owner policy predecessor digest mismatch for opportunity: {opportunity_id}"
                )


def _normalize_continuity(raw: Any) -> dict[str, Any]:
    continuity = _core._exact_keys(
        raw,
        {"schema", "ledger_id", "generation", "previous_receipt_sha256", "events"},
        "document.continuity",
    )
    if continuity["schema"] != CONTINUITY_SCHEMA:
        raise _core.PayoffPathError(
            f"document.continuity.schema must be {CONTINUITY_SCHEMA}"
        )
    ledger_id = _core._opaque_ref(continuity["ledger_id"], "document.continuity.ledger_id")
    generation = _core._int(
        continuity["generation"], "document.continuity.generation", minimum=0
    )
    previous = continuity["previous_receipt_sha256"]
    if generation == 0:
        if previous is not None:
            raise _core.PayoffPathError("generation 0 must not name a previous receipt")
        previous = None
    else:
        previous = _core._sha(previous, "document.continuity.previous_receipt_sha256")
    raw_events = continuity["events"]
    if type(raw_events) is not list:
        raise _core.PayoffPathError("document.continuity.events must be an array")
    if len(raw_events) > _MAX_EVENTS:
        raise _core.PayoffPathError("document.continuity.events exceeds event limit")
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw_event in enumerate(raw_events):
        event = _normalize_event(raw_event, f"document.continuity.events[{index}]")
        existing = by_id.get(event["event_id"])
        if existing is None:
            by_id[event["event_id"]] = event
        elif existing != event:
            raise _core.PayoffPathError(
                f"continuity event_id was replayed with changed bytes: {event['event_id']}"
            )
    events = sorted(by_id.values(), key=_event_sort_key)
    _validate_policy_chains(events)
    return {
        "schema": CONTINUITY_SCHEMA,
        "ledger_id": ledger_id,
        "generation": generation,
        "previous_receipt_sha256": previous,
        "events": events,
    }


def _normalize_document(document: Any) -> dict[str, Any]:
    root = _core._exact_keys(document, {"schema", "continuity", "work_items"}, "document")
    if root["schema"] != WORK_SCHEMA:
        raise _core.PayoffPathError(f"document.schema must be {WORK_SCHEMA}")
    return {
        "schema": WORK_SCHEMA,
        "continuity": _normalize_continuity(root["continuity"]),
        "work_items": _core._normalize_work_items(root["work_items"]),
    }


def _ledger_root(events: list[dict[str, Any]]) -> str:
    return _core._digest({"schema": CONTINUITY_SCHEMA, "events": events})


def _work_scope_sha256(work_items: list[dict[str, Any]]) -> str:
    scope = [
        {"work_id": row["work_id"], "opportunity_id": row["opportunity_id"]}
        for row in work_items
    ]
    scope.sort(key=lambda row: row["work_id"])
    return _core._digest({"schema": WORK_SCHEMA, "work_scope": scope})


def _policy_heads(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spent: dict[str, int] = {}
    heads: dict[str, dict[str, Any]] = {}
    for event in events:
        opportunity_id = event["opportunity_id"]
        if event["kind"] == "EFFORT":
            spent[opportunity_id] = spent.get(opportunity_id, 0) + event["minutes"]
            continue
        previous = heads.get(opportunity_id)
        spent_so_far = spent.get(opportunity_id, 0)
        reopened = (
            previous is not None
            and spent_so_far >= previous["cap_minutes"]
            and event["minutes"] > spent_so_far
        )
        heads[opportunity_id] = {
            "opportunity_id": opportunity_id,
            "policy_generation": event["policy_generation"],
            "cap_minutes": event["minutes"],
            "evidence_ref": event["evidence_ref"],
            "evidence_sha256": event["evidence_sha256"],
            "policy_sha256": _event_digest(event),
            "predecessor_policy_sha256": event["predecessor_policy_sha256"],
            "reopened_from_stop": reopened,
        }
    return [heads[key] for key in sorted(heads)]


def _normalize_policy_head(raw: Any, name: str) -> dict[str, Any]:
    head = _core._exact_keys(
        raw,
        {
            "opportunity_id", "policy_generation", "cap_minutes", "evidence_ref",
            "evidence_sha256", "policy_sha256", "predecessor_policy_sha256",
            "reopened_from_stop",
        },
        name,
    )
    generation = _core._int(head["policy_generation"], f"{name}.policy_generation", minimum=0)
    predecessor = head["predecessor_policy_sha256"]
    if generation == 0:
        if predecessor is not None:
            raise _core.PayoffPathError(f"{name}.predecessor_policy_sha256 must be null")
        predecessor = None
    else:
        predecessor = _core._sha(predecessor, f"{name}.predecessor_policy_sha256")
    if type(head["reopened_from_stop"]) is not bool:
        raise _core.PayoffPathError(f"{name}.reopened_from_stop must be boolean")
    return {
        "opportunity_id": _core._opaque_ref(head["opportunity_id"], f"{name}.opportunity_id"),
        "policy_generation": generation,
        "cap_minutes": _core._int(
            head["cap_minutes"], f"{name}.cap_minutes", minimum=1,
            maximum=_core._MAX_FREE_MINUTES,
        ),
        "evidence_ref": _core._opaque_ref(head["evidence_ref"], f"{name}.evidence_ref"),
        "evidence_sha256": _core._sha(head["evidence_sha256"], f"{name}.evidence_sha256"),
        "policy_sha256": _core._sha(head["policy_sha256"], f"{name}.policy_sha256"),
        "predecessor_policy_sha256": predecessor,
        "reopened_from_stop": head["reopened_from_stop"],
    }


def _normalize_receipt(receipt: Any, name: str = "previous_receipt") -> dict[str, Any]:
    obj = _core._exact_keys(
        receipt,
        {"schema", "source_document_sha256", "packet_sha256", "markdown_sha256", "continuity"},
        name,
    )
    if obj["schema"] != RECEIPT_SCHEMA:
        raise _core.PayoffPathError(f"{name}.schema must be {RECEIPT_SCHEMA}")
    for field in ("source_document_sha256", "packet_sha256", "markdown_sha256"):
        _core._sha(obj[field], f"{name}.{field}")
    c = _core._exact_keys(
        obj["continuity"],
        {
            "mode", "ledger_id", "generation", "ledger_event_count",
            "ledger_root_sha256", "previous_receipt_sha256", "work_scope_sha256",
            "policy_heads",
        },
        f"{name}.continuity",
    )
    if c["mode"] != MODE:
        raise _core.PayoffPathError(f"{name}.continuity.mode must be {MODE}")
    generation = _core._int(c["generation"], f"{name}.continuity.generation", minimum=0)
    previous = c["previous_receipt_sha256"]
    if generation == 0:
        if previous is not None:
            raise _core.PayoffPathError(f"{name} generation 0 must not name a previous receipt")
        previous = None
    else:
        previous = _core._sha(previous, f"{name}.continuity.previous_receipt_sha256")
    heads = c["policy_heads"]
    if type(heads) is not list:
        raise _core.PayoffPathError(f"{name}.continuity.policy_heads must be an array")
    normalized_heads = [
        _normalize_policy_head(head, f"{name}.continuity.policy_heads[{index}]")
        for index, head in enumerate(heads)
    ]
    normalized_heads.sort(key=lambda head: head["opportunity_id"])
    if normalized_heads != heads:
        raise _core.PayoffPathError(f"{name}.continuity.policy_heads must be canonically ordered")
    return {
        "schema": RECEIPT_SCHEMA,
        "source_document_sha256": obj["source_document_sha256"],
        "packet_sha256": obj["packet_sha256"],
        "markdown_sha256": obj["markdown_sha256"],
        "continuity": {
            "mode": MODE,
            "ledger_id": _core._opaque_ref(c["ledger_id"], f"{name}.continuity.ledger_id"),
            "generation": generation,
            "ledger_event_count": _core._int(
                c["ledger_event_count"], f"{name}.continuity.ledger_event_count",
                minimum=0, maximum=_MAX_EVENTS,
            ),
            "ledger_root_sha256": _core._sha(
                c["ledger_root_sha256"], f"{name}.continuity.ledger_root_sha256"
            ),
            "previous_receipt_sha256": previous,
            "work_scope_sha256": _core._sha(
                c["work_scope_sha256"], f"{name}.continuity.work_scope_sha256"
            ),
            "policy_heads": normalized_heads,
        },
    }


def _derive_semantics(
    normalized: dict[str, Any],
    as_of: datetime,
    previous_receipt: Any,
) -> tuple[dict[str, Any], dict[str, int], dict[str, dict[str, Any]]]:
    continuity = normalized["continuity"]
    events = continuity["events"]
    for event in events:
        if _core._timestamp(event["occurred_at_utc"], "continuity.event.occurred_at_utc") > as_of:
            raise _core.PayoffPathError(f"continuity event {event['event_id']} is from the future")
    scope_sha = _work_scope_sha256(normalized["work_items"])
    generation = continuity["generation"]
    if generation == 0:
        if previous_receipt is not None:
            raise _core.PayoffPathError("generation 0 must not be compiled with a previous receipt")
    else:
        if previous_receipt is None:
            raise _core.PayoffPathError("previous receipt is required for nonzero continuity generation")
        previous = _normalize_receipt(previous_receipt)
        anchor = previous["continuity"]
        if continuity["previous_receipt_sha256"] != _core._digest(previous):
            raise _core.PayoffPathError("previous receipt digest does not match continuity declaration")
        if continuity["ledger_id"] != anchor["ledger_id"]:
            raise _core.PayoffPathError("continuity ledger_id changed between generations")
        if generation != anchor["generation"] + 1:
            raise _core.PayoffPathError("continuity generation must advance exactly by one")
        if scope_sha != anchor["work_scope_sha256"]:
            raise _core.PayoffPathError("work/opportunity scope changed across continuity generations")
        previous_count = anchor["ledger_event_count"]
        if len(events) < previous_count:
            raise _core.PayoffPathError("continuity history is truncated")
        prefix = events[:previous_count]
        if _ledger_root(prefix) != anchor["ledger_root_sha256"]:
            raise _core.PayoffPathError("continuity history prefix does not match previous ledger root")
        if _policy_heads(prefix) != anchor["policy_heads"]:
            raise _core.PayoffPathError("owner-policy head history does not match previous receipt")
    work_by_id = {row["work_id"]: row for row in normalized["work_items"]}
    opportunities = {row["opportunity_id"] for row in normalized["work_items"]}
    for event in events:
        if event["opportunity_id"] not in opportunities:
            raise _core.PayoffPathError(
                f"continuity event targets an opportunity outside the bound work scope: {event['opportunity_id']}"
            )
        if event["kind"] == "EFFORT":
            work = work_by_id.get(event["work_id"])
            if work is None:
                raise _core.PayoffPathError(
                    f"continuity effort event targets work outside the bound work scope: {event['work_id']}"
                )
            if work["opportunity_id"] != event["opportunity_id"]:
                raise _core.PayoffPathError(
                    f"continuity effort event {event['event_id']} binds work_id to a different opportunity"
                )
            if _core._timestamp(event["occurred_at_utc"], "continuity effort time") < _core._timestamp(
                work["started_at_utc"], "work start"
            ):
                raise _core.PayoffPathError(
                    f"continuity effort event {event['event_id']} predates its work start"
                )
    heads_list = _policy_heads(events)
    heads = {head["opportunity_id"]: head for head in heads_list}
    spent: dict[str, int] = {}
    first_policy_time: dict[str, str] = {}
    for event in events:
        opportunity_id = event["opportunity_id"]
        if event["kind"] == "BUDGET_POLICY":
            first_policy_time.setdefault(opportunity_id, event["occurred_at_utc"])
        else:
            if opportunity_id not in first_policy_time:
                raise _core.PayoffPathError(
                    f"effort precedes first owner budget policy for opportunity: {opportunity_id}"
                )
            spent[opportunity_id] = spent.get(opportunity_id, 0) + event["minutes"]
            if spent[opportunity_id] > _core._MAX_FREE_MINUTES:
                raise _core.PayoffPathError(
                    f"cumulative effort exceeds safe limit for opportunity: {opportunity_id}"
                )
    for row in normalized["work_items"]:
        opportunity_id = row["opportunity_id"]
        head = heads.get(opportunity_id)
        if head is None:
            raise _core.PayoffPathError(
                f"missing owner budget policy for opportunity: {opportunity_id}"
            )
        expected_spent = spent.get(opportunity_id, 0)
        if row["free_work_budget_minutes"] != head["cap_minutes"]:
            raise _core.PayoffPathError(
                f"work item {row['work_id']} budget does not match current owner policy"
            )
        if row["free_work_spent_minutes"] != expected_spent:
            raise _core.PayoffPathError(
                f"work item {row['work_id']} spent minutes do not match cumulative evidence-bound effort"
            )
    summary = {
        "mode": MODE,
        "ledger_id": continuity["ledger_id"],
        "generation": generation,
        "ledger_event_count": len(events),
        "ledger_root_sha256": _ledger_root(events),
        "previous_receipt_sha256": continuity["previous_receipt_sha256"],
        "work_scope_sha256": scope_sha,
        "policy_heads": heads_list,
    }
    return summary, spent, heads


def _evaluate_item(
    item: dict[str, Any], as_of: datetime, policy_head: dict[str, Any]
) -> dict[str, Any]:
    row = _core._evaluate_item(item, as_of)
    row["owner_policy"] = dict(policy_head)
    if policy_head["reopened_from_stop"] and row["state"] == "READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW":
        row["reasons"].append("OWNER_POLICY_SUPERSESSION_REOPENED_AFTER_STOP")
    elif policy_head["policy_generation"] > 0:
        row["reasons"].append("OWNER_POLICY_SUPERSESSION_ACTIVE")
    return row


def render_markdown(packet: dict[str, Any]) -> str:
    if type(packet) is not dict or packet.get("schema") != PACKET_SCHEMA:
        raise _core.PayoffPathError(f"packet must be {PACKET_SCHEMA}")
    shadow = dict(packet)
    shadow["schema"] = _core.PACKET_SCHEMA
    shadow["results"] = [
        {key: value for key, value in row.items() if key != "owner_policy"}
        for row in packet["results"]
    ]
    base = _core.render_markdown(shadow).rstrip()
    lines = [base, "", "## Evidence-bound owner policy continuity", ""]
    for head in packet["continuity"]["policy_heads"]:
        reopen = "yes" if head["reopened_from_stop"] else "no"
        lines.append(
            f"- `{head['opportunity_id']}` policy generation `{head['policy_generation']}`: "
            f"cap `{head['cap_minutes']}` min; evidence `{head['evidence_ref']}` / "
            f"`{head['evidence_sha256']}`; policy `{head['policy_sha256']}`; "
            f"reopened after prior STOP: `{reopen}`"
        )
    lines.extend(
        [
            "",
            "> Owner-policy supersession is evidence, not an external action. This gate still does not authorize outreach, submission, spend, delivery, payout, payment recognition, or revenue recognition.",
            "",
        ]
    )
    return "\n".join(lines)


def _compile_v3(
    document: Any,
    trusted_as_of: datetime | str | None,
    previous_receipt: Any,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    as_of = _core._trusted_now(trusted_as_of)
    normalized = _normalize_document(document)
    continuity, _spent, heads = _derive_semantics(normalized, as_of, previous_receipt)
    rows = [
        _evaluate_item(item, as_of, heads[item["opportunity_id"]])
        for item in normalized["work_items"]
    ]
    packet = {
        "schema": PACKET_SCHEMA,
        "evaluated_at_utc": _core._render_timestamp(as_of),
        "source_document_sha256": _core._digest(normalized),
        "continuity": continuity,
        "summary": _core._summary(rows),
        "results": rows,
        "authority": "OWNER_REVIEW_ONLY_NO_EXTERNAL_ACTION",
    }
    markdown = render_markdown(packet)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "source_document_sha256": packet["source_document_sha256"],
        "packet_sha256": _core._digest(packet),
        "markdown_sha256": _core._sha256_bytes(markdown.encode("utf-8")),
        "continuity": continuity,
    }
    return packet, markdown, receipt


def verify_v3(
    document: Any,
    packet: Any,
    markdown: Any,
    receipt: Any,
    trusted_now: datetime | str | None = None,
    previous_receipt: Any = None,
) -> bool:
    now = _core._trusted_now(trusted_now)
    normalized = _normalize_document(document)
    packet_obj = _core._exact_keys(
        packet,
        {
            "schema", "evaluated_at_utc", "source_document_sha256", "continuity",
            "summary", "results", "authority",
        },
        "packet",
    )
    if packet_obj["schema"] != PACKET_SCHEMA:
        raise _core.PayoffPathError(f"packet.schema must be {PACKET_SCHEMA}")
    if packet_obj["authority"] != "OWNER_REVIEW_ONLY_NO_EXTERNAL_ACTION":
        raise _core.PayoffPathError("packet authority is invalid")
    evaluated = _core._timestamp(packet_obj["evaluated_at_utc"], "packet.evaluated_at_utc")
    if evaluated > now:
        raise _core.PayoffPathError("packet evaluation time is in the future")
    if packet_obj["source_document_sha256"] != _core._digest(normalized):
        raise _core.PayoffPathError("packet source document digest mismatch")
    expected_packet, expected_markdown, expected_receipt = _compile_v3(
        normalized, evaluated, previous_receipt
    )
    if packet_obj != expected_packet:
        raise _core.PayoffPathError("packet content does not match recompilation")
    if type(markdown) is not str or markdown != expected_markdown:
        raise _core.PayoffPathError("Markdown content does not match recompilation")
    receipt_obj = _normalize_receipt(receipt, "receipt")
    if receipt_obj != expected_receipt:
        raise _core.PayoffPathError("receipt content does not match exact packet/Markdown")
    current_continuity, _spent, heads = _derive_semantics(normalized, now, previous_receipt)
    if current_continuity != expected_packet["continuity"]:
        raise _core.PayoffPathError("continuity semantics changed at verification time")
    current_rows = [
        _evaluate_item(item, now, heads[item["opportunity_id"]])
        for item in normalized["work_items"]
    ]
    previous_rows = {row["work_id"]: row for row in expected_packet["results"]}
    for row in current_rows:
        before = previous_rows[row["work_id"]]
        if (
            before["state"] == "READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW"
            and row["state"] != before["state"]
        ):
            raise _core.PayoffPathError(
                f"previous READY item {row['work_id']} is no longer current at trusted verification time"
            )
    return True


def compile_legacy_migration_gate(
    document: Any,
    trusted_as_of: datetime | str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Historical v1 replay surface; never used by normal library/CLI compilation."""
    if trusted_as_of is None:
        raise _core.PayoffPathError("legacy migration replay requires explicit trusted_as_of")
    if type(document) is not dict or document.get("schema") != _core.LEGACY_WORK_SCHEMA:
        raise _core.PayoffPathError(
            f"legacy migration replay requires {_core.LEGACY_WORK_SCHEMA} input"
        )
    return _ORIGINAL_INTERNAL_COMPILE(document, trusted_as_of, None, legacy_replay=True)


def verify_legacy_migration_gate(
    document: Any,
    packet: Any,
    markdown: Any,
    receipt: Any,
    trusted_now: datetime | str | None = None,
) -> bool:
    """Verify a packet produced by :func:`compile_legacy_migration_gate`."""
    if type(document) is not dict or document.get("schema") != _core.LEGACY_WORK_SCHEMA:
        raise _core.PayoffPathError("legacy migration verification requires legacy v1 input")
    now = _core._trusted_now(trusted_now)
    if type(packet) is not dict:
        raise _core.PayoffPathError("packet must be an object")
    evaluated = _core._timestamp(packet.get("evaluated_at_utc"), "packet.evaluated_at_utc")
    if evaluated > now:
        raise _core.PayoffPathError("packet evaluation time is in the future")
    expected_packet, expected_markdown, expected_receipt = _ORIGINAL_INTERNAL_COMPILE(
        document, evaluated, None, legacy_replay=True
    )
    if packet != expected_packet or markdown != expected_markdown or receipt != expected_receipt:
        raise _core.PayoffPathError("legacy migration packet does not match exact recompilation")
    current_packet, _current_markdown, _current_receipt = _ORIGINAL_INTERNAL_COMPILE(
        document, now, None, legacy_replay=True
    )
    current = {row["work_id"]: row for row in current_packet["results"]}
    for before in expected_packet["results"]:
        after = current[before["work_id"]]
        if (
            before["state"] == "READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW"
            and after["state"] != before["state"]
        ):
            raise _core.PayoffPathError(
                f"legacy migration READY item {before['work_id']} is no longer current"
            )
    return True


def _dispatch_internal_compile(
    document: Any,
    trusted_as_of: datetime | str | None,
    previous_receipt: Any,
    *,
    legacy_replay: bool,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    schema = document.get("schema") if type(document) is dict else None
    if schema == WORK_SCHEMA:
        return _compile_v3(document, trusted_as_of, previous_receipt)
    if schema == _core.LEGACY_WORK_SCHEMA:
        return _ORIGINAL_INTERNAL_COMPILE(
            document, trusted_as_of, previous_receipt, legacy_replay=False
        )
    return _ORIGINAL_INTERNAL_COMPILE(
        document, trusted_as_of, previous_receipt, legacy_replay=legacy_replay
    )


def _dispatch_verify(
    document: Any,
    packet: Any,
    markdown: Any,
    receipt: Any,
    trusted_now: datetime | str | None = None,
    previous_receipt: Any = None,
) -> bool:
    schema = document.get("schema") if type(document) is dict else None
    if schema == WORK_SCHEMA:
        return verify_v3(
            document, packet, markdown, receipt,
            trusted_now=trusted_now, previous_receipt=previous_receipt,
        )
    mode = None
    if type(packet) is dict and type(packet.get("continuity")) is dict:
        mode = packet["continuity"].get("mode")
    if schema == _core.LEGACY_WORK_SCHEMA and mode == "LEGACY_REPLAY_ONLY":
        raise _core.PayoffPathError(
            "legacy READY replay is migration-only; use verify_legacy_migration_gate"
        )
    return _ORIGINAL_VERIFY(
        document, packet, markdown, receipt,
        trusted_now=trusted_now, previous_receipt=previous_receipt,
    )


def install() -> None:
    """Install v3 dispatch into the landed semantic core exactly once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _core._compile_gate = _dispatch_internal_compile
    _core.verify_gate = _dispatch_verify
    _core.compile_legacy_migration_gate = compile_legacy_migration_gate
    _core.verify_legacy_migration_gate = verify_legacy_migration_gate
    _core.WORK_SCHEMA_V3 = WORK_SCHEMA
    _core.CONTINUITY_SCHEMA_V2 = CONTINUITY_SCHEMA
    _core.PACKET_SCHEMA_V3 = PACKET_SCHEMA
    _core.RECEIPT_SCHEMA_V3 = RECEIPT_SCHEMA
    _core.POLICY_EVENT_SHA256 = policy_event_sha256
    _core.compile_gate.__doc__ = (
        "Compile owner-review evidence. Normal v1 input is always fail-closed; use "
        "compile_legacy_migration_gate only for explicit historical migration replay. "
        "v2 uses the landed continuity chain; v3 adds evidence-bound effort and "
        "versioned owner-policy supersession."
    )
    _INSTALLED = True


__all__ = [
    "WORK_SCHEMA", "CONTINUITY_SCHEMA", "PACKET_SCHEMA", "RECEIPT_SCHEMA",
    "compile_legacy_migration_gate", "verify_legacy_migration_gate",
    "policy_event_sha256", "render_markdown", "verify_v3", "install",
]
