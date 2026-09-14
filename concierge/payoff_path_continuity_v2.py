# SPDX-License-Identifier: MIT
"""Evidence-bound effort + explicit owner budget-policy continuity.

Legacy-only ledgers keep the exact landed v1 path. Extended semantics activate only
when an evidenced EFFORT or BUDGET_POLICY event is present, so an existing v1 receipt
can migrate by appending events without rewriting its anchored prefix/root.
"""
from __future__ import annotations

from typing import Any

BUDGET_POLICY_KIND = "BUDGET_POLICY"
CONTINUITY_SEMANTICS = "EVIDENCE_BOUND_OWNER_POLICY_V2"

_BASE = {"event_id", "kind", "opportunity_id", "work_id", "minutes", "occurred_at_utc"}
_EFFORT = _BASE | {"evidence_ref", "evidence_sha256"}
_POLICY = _EFFORT | {"policy_generation", "supersedes_policy_sha256"}
_RECEIPT_CONTINUITY_KEYS = {
    "mode", "ledger_id", "generation", "ledger_event_count",
    "ledger_root_sha256", "previous_receipt_sha256",
}


def _is_extended(event: Any) -> bool:
    return type(event) is dict and (
        event.get("kind") == BUDGET_POLICY_KIND
        or (event.get("kind") == "EFFORT" and ("evidence_ref" in event or "evidence_sha256" in event))
    )


def install(core: Any) -> None:
    if getattr(core, "_PAYOFF_CONTINUITY_V2_INSTALLED", False):
        return

    old_normalize = core._normalize_continuity
    old_summary = core._continuity_summary
    old_render = core.render_markdown
    old_compile = core._compile_gate

    def base_event(raw: Any, index: int, keys: set[str]) -> dict[str, Any]:
        event = core._exact_keys(raw, keys, f"document.continuity.events[{index}]")
        return {
            "event_id": core._opaque_ref(event["event_id"], f"document.continuity.events[{index}].event_id"),
            "kind": core._text(event["kind"], f"document.continuity.events[{index}].kind"),
            "opportunity_id": core._opaque_ref(
                event["opportunity_id"], f"document.continuity.events[{index}].opportunity_id"
            ),
            "work_id": event["work_id"],
            "minutes": core._int(
                event["minutes"], f"document.continuity.events[{index}].minutes",
                minimum=1, maximum=core._MAX_FREE_MINUTES,
            ),
            "occurred_at_utc": core._render_timestamp(
                core._timestamp(event["occurred_at_utc"], f"document.continuity.events[{index}].occurred_at_utc")
            ),
        }

    def normalize_extended(raw: Any) -> dict[str, Any]:
        c = core._exact_keys(
            raw, {"schema", "ledger_id", "generation", "previous_receipt_sha256", "events"},
            "document.continuity",
        )
        if c["schema"] != core.CONTINUITY_SCHEMA:
            raise core.PayoffPathError(f"document.continuity.schema must be {core.CONTINUITY_SCHEMA}")
        ledger_id = core._opaque_ref(c["ledger_id"], "document.continuity.ledger_id")
        generation = core._int(c["generation"], "document.continuity.generation", minimum=0)
        previous = c["previous_receipt_sha256"]
        if generation == 0:
            if previous is not None:
                raise core.PayoffPathError("generation 0 must not name a previous receipt")
        else:
            previous = core._sha(previous, "document.continuity.previous_receipt_sha256")
        if type(c["events"]) is not list:
            raise core.PayoffPathError("document.continuity.events must be an array")
        if len(c["events"]) > core._MAX_EVENTS:
            raise core.PayoffPathError("document.continuity.events exceeds event limit")

        events: list[dict[str, Any]] = []
        by_id: dict[str, dict[str, Any]] = {}
        for index, raw_event in enumerate(c["events"]):
            if type(raw_event) is not dict:
                raise core.PayoffPathError(f"document.continuity.events[{index}] must be an object")
            kind = core._text(raw_event.get("kind"), f"document.continuity.events[{index}].kind")
            if kind == "BUDGET_SET":
                event = base_event(raw_event, index, _BASE)
                if event["work_id"] is not None:
                    raise core.PayoffPathError("BUDGET_SET event work_id must be null")
                event["work_id"] = None
            elif kind == "EFFORT":
                evidenced = set(raw_event) == _EFFORT
                if not evidenced and set(raw_event) != _BASE:
                    raise core.PayoffPathError("EFFORT event must use the exact legacy or evidenced shape")
                event = base_event(raw_event, index, _EFFORT if evidenced else _BASE)
                event["work_id"] = core._opaque_ref(
                    event["work_id"], f"document.continuity.events[{index}].work_id"
                )
                if evidenced:
                    event["evidence_ref"] = core._opaque_ref(
                        raw_event["evidence_ref"], f"document.continuity.events[{index}].evidence_ref"
                    )
                    event["evidence_sha256"] = core._sha(
                        raw_event["evidence_sha256"], f"document.continuity.events[{index}].evidence_sha256"
                    )
            elif kind == BUDGET_POLICY_KIND:
                event = base_event(raw_event, index, _POLICY)
                if event["work_id"] is not None:
                    raise core.PayoffPathError("BUDGET_POLICY event work_id must be null")
                event["work_id"] = None
                event["evidence_ref"] = core._opaque_ref(
                    raw_event["evidence_ref"], f"document.continuity.events[{index}].evidence_ref"
                )
                event["evidence_sha256"] = core._sha(
                    raw_event["evidence_sha256"], f"document.continuity.events[{index}].evidence_sha256"
                )
                pg = core._int(
                    raw_event["policy_generation"],
                    f"document.continuity.events[{index}].policy_generation", minimum=0,
                )
                predecessor = raw_event["supersedes_policy_sha256"]
                if pg == 0:
                    if predecessor is not None:
                        raise core.PayoffPathError("BUDGET_POLICY generation 0 cannot supersede a policy")
                else:
                    predecessor = core._sha(
                        predecessor,
                        f"document.continuity.events[{index}].supersedes_policy_sha256",
                    )
                event["policy_generation"] = pg
                event["supersedes_policy_sha256"] = predecessor
            else:
                raise core.PayoffPathError(f"document.continuity.events[{index}].kind is unsupported")

            prior = by_id.get(event["event_id"])
            if prior is not None:
                if prior != event:
                    raise core.PayoffPathError(
                        f"continuity event_id replay changed immutable facts: {event['event_id']}"
                    )
                continue
            by_id[event["event_id"]] = event
            events.append(event)

        events.sort(key=lambda event: event["occurred_at_utc"])

        policies: dict[str, dict[int, dict[str, Any]]] = {}
        efforts: list[dict[str, Any]] = []
        for event in events:
            opp = event["opportunity_id"]
            if event["kind"] == "BUDGET_SET":
                bucket = policies.setdefault(opp, {})
                if 0 in bucket:
                    raise core.PayoffPathError(f"budget already declared for opportunity: {opp}")
                bucket[0] = event
            elif event["kind"] == BUDGET_POLICY_KIND:
                bucket = policies.setdefault(opp, {})
                pg = event["policy_generation"]
                if pg in bucket:
                    raise core.PayoffPathError(
                        f"budget policy generation already declared for opportunity {opp}: {pg}"
                    )
                bucket[pg] = event
            else:
                efforts.append(event)

        for opp, chain in policies.items():
            gens = sorted(chain)
            if not gens or gens != list(range(gens[-1] + 1)):
                raise core.PayoffPathError(f"budget policy generations must be contiguous for opportunity: {opp}")
            for pg in gens:
                policy = chain[pg]
                if pg == 0:
                    if policy["kind"] == BUDGET_POLICY_KIND and policy["supersedes_policy_sha256"] is not None:
                        raise core.PayoffPathError("initial budget policy cannot supersede another policy")
                    continue
                if policy["kind"] != BUDGET_POLICY_KIND:
                    raise core.PayoffPathError("only BUDGET_POLICY can supersede an existing budget")
                predecessor = chain[pg - 1]
                if policy["supersedes_policy_sha256"] != core._digest(predecessor):
                    raise core.PayoffPathError(
                        f"budget policy predecessor digest mismatch for opportunity {opp} generation {pg}"
                    )
                if core._timestamp(policy["occurred_at_utc"], "policy time") < core._timestamp(
                    predecessor["occurred_at_utc"], "predecessor policy time"
                ):
                    raise core.PayoffPathError("budget policy supersession predates its predecessor")

        for effort in efforts:
            opp = effort["opportunity_id"]
            if opp not in policies:
                raise core.PayoffPathError(f"EFFORT event has no budget policy for opportunity: {opp}")
            initial = policies[opp][0]
            effort_time = core._timestamp(effort["occurred_at_utc"], "effort time")
            if effort_time < core._timestamp(initial["occurred_at_utc"], "initial policy time"):
                raise core.PayoffPathError(f"EFFORT event predates initial budget policy for opportunity: {opp}")
            owner_policies = [p for p in policies[opp].values() if p["kind"] == BUDGET_POLICY_KIND]
            if owner_policies:
                migration_time = min(core._timestamp(p["occurred_at_utc"], "policy migration time") for p in owner_policies)
                if effort_time >= migration_time and "evidence_ref" not in effort:
                    raise core.PayoffPathError(
                        f"EFFORT after owner-policy migration must be evidence-bound for opportunity: {opp}"
                    )

        return {
            "schema": core.CONTINUITY_SCHEMA,
            "ledger_id": ledger_id,
            "generation": generation,
            "previous_receipt_sha256": previous,
            "events": events,
        }

    def normalize(raw: Any) -> dict[str, Any]:
        if type(raw) is dict and type(raw.get("events")) is list and any(_is_extended(e) for e in raw["events"]):
            return normalize_extended(raw)
        return old_normalize(raw)

    def extended_document(document: Any) -> bool:
        c = document.get("continuity") if type(document) is dict else None
        return type(c) is dict and any(_is_extended(e) for e in c.get("events", []))

    def analyze(events: list[dict[str, Any]], work_items: list[dict[str, Any]]):
        policies: dict[str, dict[int, dict[str, Any]]] = {}
        efforts: list[dict[str, Any]] = []
        evidence_effort = legacy_effort = owner_policies = 0
        for event in events:
            opp = event["opportunity_id"]
            if event["kind"] == "BUDGET_SET":
                policies.setdefault(opp, {})[0] = event
            elif event["kind"] == BUDGET_POLICY_KIND:
                policies.setdefault(opp, {})[event["policy_generation"]] = event
                owner_policies += 1
            else:
                efforts.append(event)
                if "evidence_ref" in event:
                    evidence_effort += 1
                else:
                    legacy_effort += 1

        current_work = {row["work_id"]: row for row in work_items}
        spent: dict[str, int] = {}
        for event in efforts:
            work = current_work.get(event["work_id"])
            if work is None:
                raise core.PayoffPathError(f"continuity effort event {event['event_id']} names unknown work_id")
            if work["opportunity_id"] != event["opportunity_id"]:
                raise core.PayoffPathError(
                    f"continuity effort event {event['event_id']} binds work_id to a different opportunity"
                )
            if core._timestamp(event["occurred_at_utc"], "effort time") < core._timestamp(
                work["started_at_utc"], "work start"
            ):
                raise core.PayoffPathError(f"continuity effort event {event['event_id']} predates its work start")
            opp = event["opportunity_id"]
            spent[opp] = spent.get(opp, 0) + event["minutes"]
            if spent[opp] > core._MAX_FREE_MINUTES:
                raise core.PayoffPathError(f"cumulative effort exceeds safe limit for opportunity: {opp}")

        active_budget: dict[str, int] = {}
        history: list[dict[str, Any]] = []
        for opp in sorted(policies):
            chain = policies[opp]
            previous_policy = None
            for pg in sorted(chain):
                policy = chain[pg]
                reopened = False
                if previous_policy is not None:
                    policy_time = core._timestamp(policy["occurred_at_utc"], "policy time")
                    spent_at_transition = sum(
                        e["minutes"] for e in efforts
                        if e["opportunity_id"] == opp
                        and core._timestamp(e["occurred_at_utc"], "effort time") <= policy_time
                    )
                    reopened = spent_at_transition >= previous_policy["minutes"] and policy["minutes"] > spent_at_transition
                history.append({
                    "opportunity_id": opp,
                    "policy_generation": pg,
                    "budget_minutes": policy["minutes"],
                    "policy_event_id": policy["event_id"],
                    "policy_kind": policy["kind"],
                    "policy_sha256": core._digest(policy),
                    "evidence_ref": policy.get("evidence_ref"),
                    "evidence_sha256": policy.get("evidence_sha256"),
                    "supersedes_policy_sha256": policy.get("supersedes_policy_sha256"),
                    "reopened_from_stop": reopened,
                })
                previous_policy = policy
            active_budget[opp] = chain[max(chain)]["minutes"]
        return active_budget, spent, history, evidence_effort, legacy_effort, owner_policies

    def summary(normalized: dict[str, Any], as_of: Any, previous_receipt: Any, *, legacy_replay: bool):
        if not extended_document(normalized):
            return old_summary(normalized, as_of, previous_receipt, legacy_replay=legacy_replay)
        c = normalized["continuity"]
        events = c["events"]
        for event in events:
            if core._timestamp(event["occurred_at_utc"], "continuity event time") > as_of:
                raise core.PayoffPathError(f"continuity event {event['event_id']} is from the future")

        generation = c["generation"]
        if generation == 0:
            if previous_receipt is not None:
                raise core.PayoffPathError("generation 0 must not be compiled with a previous receipt")
        else:
            if previous_receipt is None:
                raise core.PayoffPathError("previous receipt is required for nonzero continuity generation")
            previous = core._normalize_receipt_anchor(previous_receipt)
            anchor = previous["continuity"]
            if anchor["mode"] != "CHAINED":
                raise core.PayoffPathError("previous receipt is not a chained continuity anchor")
            if c["previous_receipt_sha256"] != core._digest(previous):
                raise core.PayoffPathError("previous receipt digest does not match continuity declaration")
            if c["ledger_id"] != anchor["ledger_id"]:
                raise core.PayoffPathError("continuity ledger_id changed between generations")
            if generation != anchor["generation"] + 1:
                raise core.PayoffPathError("continuity generation must advance exactly by one")
            previous_count = anchor["ledger_event_count"]
            if len(events) < previous_count:
                raise core.PayoffPathError("continuity history is truncated")
            if core._ledger_root(events[:previous_count]) != anchor["ledger_root_sha256"]:
                raise core.PayoffPathError("continuity history prefix does not match previous ledger root")

        budgets, spent, history, evidenced, legacy, owner_policy_count = analyze(events, normalized["work_items"])
        for row in normalized["work_items"]:
            opp = row["opportunity_id"]
            if opp not in budgets:
                raise core.PayoffPathError(f"missing continuity budget declaration for opportunity: {opp}")
            if row["free_work_budget_minutes"] != budgets[opp]:
                raise core.PayoffPathError(
                    f"work item {row['work_id']} budget does not match active owner budget policy"
                )
            if row["free_work_spent_minutes"] != spent.get(opp, 0):
                raise core.PayoffPathError(
                    f"work item {row['work_id']} spent minutes do not match cumulative continuity effort"
                )
        return {
            "mode": "CHAINED",
            "ledger_id": c["ledger_id"],
            "generation": generation,
            "ledger_event_count": len(events),
            "ledger_root_sha256": core._ledger_root(events),
            "previous_receipt_sha256": c["previous_receipt_sha256"],
            "semantics": CONTINUITY_SEMANTICS,
            "evidence_bound_effort_event_count": evidenced,
            "legacy_effort_event_count": legacy,
            "owner_policy_event_count": owner_policy_count,
            "budget_policy_history": history,
        }

    def render(packet: dict[str, Any]) -> str:
        text = old_render(packet)
        c = packet.get("continuity") if type(packet) is dict else None
        if type(c) is not dict or c.get("semantics") != CONTINUITY_SEMANTICS:
            return text
        lines = [
            "", "## Continuity policy custody", "",
            f"- Semantics: `{CONTINUITY_SEMANTICS}`",
            f"- Evidence-bound effort events: `{c['evidence_bound_effort_event_count']}`; legacy retained: `{c['legacy_effort_event_count']}`",
            f"- Explicit owner budget-policy events: `{c['owner_policy_event_count']}`",
        ]
        for policy in c["budget_policy_history"]:
            evidence = "legacy policy" if policy["evidence_ref"] is None else f"evidence `{policy['evidence_ref']}` / `{policy['evidence_sha256']}`"
            reopen = "; **STOP→reopen explicitly authorized**" if policy["reopened_from_stop"] else ""
            lines.append(
                f"- `{policy['opportunity_id']}` gen `{policy['policy_generation']}`: `{policy['budget_minutes']}` min; "
                f"`{policy['policy_kind']}`; {evidence}; digest `{policy['policy_sha256']}`{reopen}"
            )
        marker = "\n\n> READY means owner review only."
        if marker not in text:
            raise core.PayoffPathError("internal Markdown continuity insertion point missing")
        return text.replace(marker, "\n".join(lines) + marker, 1)

    def compile_gate(*args: Any, **kwargs: Any):
        packet, markdown, receipt = old_compile(*args, **kwargs)
        c = packet.get("continuity") if type(packet) is dict else None
        if type(c) is dict and c.get("semantics") == CONTINUITY_SEMANTICS:
            receipt = dict(receipt)
            receipt["continuity"] = {key: c[key] for key in _RECEIPT_CONTINUITY_KEYS}
        return packet, markdown, receipt

    core.BUDGET_POLICY_KIND = BUDGET_POLICY_KIND
    core.CONTINUITY_SEMANTICS_V2 = CONTINUITY_SEMANTICS
    core.EVENT_KINDS = set(core.EVENT_KINDS) | {BUDGET_POLICY_KIND}
    core._normalize_continuity = normalize
    core._continuity_summary = summary
    core.render_markdown = render
    core._compile_gate = compile_gate
    core._PAYOFF_CONTINUITY_V2_INSTALLED = True
