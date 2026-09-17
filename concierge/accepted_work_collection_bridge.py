# SPDX-License-Identifier: MIT
"""Compose dedicated work-acceptance evidence with certified settlement review.

The existing settlement ledger/certifier/collection queue remain authoritative
for award, payout, transfer, payment, and closure truth. This module restores one
commercial distinction preserved by the canonical predecessor: an exact merged
work item can have explicit sponsor/provider acceptance evidence without that
acceptance proving an award, debt, transfer, payment, or accounting revenue.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .certified_settlement_collection_queue import build_queue
from .reward_settlement_certifier import CertificationError, canonical, loads_strict, verify_registry

ACCEPTANCE_SCHEMA = "bounty-concierge/reward-settlement-work-acceptance/v1"
BRIDGE_SCHEMA = "bounty-concierge/accepted-work-collection-bridge/v1"
MAX_RECORDS = 10_000
MAX_FRESHNESS_SECONDS = 366 * 86400

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+\-]{0,239}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_ACCEPTANCE_AUTHORITIES = frozenset({"SPONSOR", "PROVIDER"})
_COLLECTION_CANDIDATES = frozenset({
    "AWARD_FOLLOWUP_CANDIDATE",
    "PAYOUT_TICKET_FOLLOWUP_CANDIDATE",
    "PAYOUT_RAIL_FOLLOWUP_CANDIDATE",
    "TRANSFER_PENDING_FOLLOWUP_CANDIDATE",
})


class AcceptedWorkBridgeError(ValueError):
    """Malformed, stale, unbound, or contradictory acceptance composition."""


def _exact(value: Any, keys: Iterable[str], name: str) -> dict[str, Any]:
    expected = set(keys)
    if type(value) is not dict or set(value) != expected:
        raise AcceptedWorkBridgeError(f"{name} must have exact keys {sorted(expected)}")
    return value


def _token(value: Any, name: str) -> str:
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise AcceptedWorkBridgeError(f"{name} must be bounded token text")
    return value


def _timestamp(value: Any, name: str) -> tuple[str, datetime]:
    if type(value) is not str or _TS.fullmatch(value) is None:
        raise AcceptedWorkBridgeError(f"{name} must be canonical UTC seconds")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise AcceptedWorkBridgeError(f"{name} must be a real UTC timestamp") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise AcceptedWorkBridgeError(f"{name} must be canonical UTC seconds")
    return value, parsed


def _age(earlier: datetime, later: datetime, name: str) -> int:
    seconds = int((later - earlier).total_seconds())
    if seconds < 0:
        raise AcceptedWorkBridgeError(f"{name} is in the future")
    return seconds


def _work(value: Any, name: str) -> dict[str, Any]:
    value = _exact(value, {"repo", "pr", "merge_commit_sha"}, name)
    repo = value["repo"]
    pr = value["pr"]
    sha = value["merge_commit_sha"]
    if type(repo) is not str or _REPO.fullmatch(repo) is None:
        raise AcceptedWorkBridgeError(f"{name}.repo must be owner/name")
    if type(pr) is not int or isinstance(pr, bool) or pr <= 0:
        raise AcceptedWorkBridgeError(f"{name}.pr must be a positive integer")
    if type(sha) is not str or _SHA1.fullmatch(sha) is None:
        raise AcceptedWorkBridgeError(f"{name}.merge_commit_sha must be lowercase 40-hex")
    return {"repo": repo, "pr": pr, "merge_commit_sha": sha}


def _source(value: Any, name: str) -> dict[str, str]:
    value = _exact(
        value,
        {"source_id", "source_ref", "source_sha256", "observed_at", "authority"},
        name,
    )
    source = {
        "source_id": _token(value["source_id"], f"{name}.source_id"),
        "source_ref": _token(value["source_ref"], f"{name}.source_ref"),
        "source_sha256": value["source_sha256"],
        "observed_at": value["observed_at"],
        "authority": value["authority"],
    }
    if type(source["source_sha256"]) is not str or _HEX64.fullmatch(source["source_sha256"]) is None:
        raise AcceptedWorkBridgeError(f"{name}.source_sha256 must be lowercase sha256")
    _timestamp(source["observed_at"], f"{name}.observed_at")
    if source["authority"] not in _ACCEPTANCE_AUTHORITIES:
        raise AcceptedWorkBridgeError(
            f"{name}.authority must be SPONSOR or PROVIDER; repository merge/approval is not acceptance"
        )
    return source


def _validated_cases(document: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Index already-upstream-validated cases and reserve settlement source ids."""
    cases = document.get("cases")
    if type(cases) is not list:
        raise AcceptedWorkBridgeError("settlement document cases must be a list")
    by_case: dict[str, dict[str, Any]] = {}
    used_source_ids: set[str] = set()
    for index, case in enumerate(cases):
        if type(case) is not dict or "case_id" not in case or "work" not in case or "events" not in case:
            raise AcceptedWorkBridgeError(f"settlement cases[{index}] lacks case_id/work/events")
        case_id = _token(case["case_id"], f"settlement cases[{index}].case_id")
        if case_id in by_case:
            raise AcceptedWorkBridgeError(f"duplicate settlement case_id: {case_id}")
        by_case[case_id] = case
        sources = [case["work"]["source"], *[event["source"] for event in case["events"]]]
        used_source_ids.update(source["source_id"] for source in sources)
    return by_case, used_source_ids


def _acceptance_records(
    document: dict[str, Any],
    manifest: Any,
    trusted_sources: dict[str, dict[str, str]],
    *,
    as_of_dt: datetime,
    registry_generated_dt: datetime,
    freshness_seconds: int,
) -> tuple[dict[str, dict[str, Any]], str]:
    manifest = _exact(manifest, {"schema", "generated_at", "records"}, "acceptance")
    if manifest["schema"] != ACCEPTANCE_SCHEMA:
        raise AcceptedWorkBridgeError(f"acceptance.schema must be {ACCEPTANCE_SCHEMA}")
    manifest_at, manifest_dt = _timestamp(manifest["generated_at"], "acceptance.generated_at")
    _age(manifest_dt, as_of_dt, "acceptance.generated_at")
    if manifest_dt > registry_generated_dt:
        raise AcceptedWorkBridgeError(
            "acceptance manifest may not postdate the signed trusted-source registry generation"
        )
    records = manifest["records"]
    if type(records) is not list or len(records) > MAX_RECORDS:
        raise AcceptedWorkBridgeError("acceptance.records must be a bounded list")

    by_case, settlement_source_ids = _validated_cases(document)
    accepted: dict[str, dict[str, Any]] = {}
    acceptance_source_owner: dict[str, str] = {}

    for index, raw in enumerate(records):
        name = f"acceptance.records[{index}]"
        row = _exact(raw, {"case_id", "work", "decision", "source"}, name)
        case_id = _token(row["case_id"], f"{name}.case_id")
        if case_id in accepted:
            raise AcceptedWorkBridgeError(f"duplicate acceptance record for case_id: {case_id}")
        case = by_case.get(case_id)
        if case is None:
            raise AcceptedWorkBridgeError(f"{name}.case_id does not exist in settlement document")
        if row["decision"] != "ACCEPTED":
            raise AcceptedWorkBridgeError(f"{name}.decision must be ACCEPTED")

        settlement_work = case["work"]
        expected_work = {
            "repo": settlement_work["repo"],
            "pr": settlement_work["pr"],
            "merge_commit_sha": settlement_work["merge_commit_sha"],
        }
        if _work(row["work"], f"{name}.work") != expected_work:
            raise AcceptedWorkBridgeError(f"{name}.work does not bind exact settlement work")

        source = _source(row["source"], f"{name}.source")
        source_id = source["source_id"]
        if source_id in settlement_source_ids:
            raise AcceptedWorkBridgeError(
                f"{name}.source must be dedicated acceptance evidence, not a relabeled settlement source"
            )
        owner = acceptance_source_owner.get(source_id)
        if owner is not None and owner != case_id:
            raise AcceptedWorkBridgeError(
                f"acceptance source_id {source_id} was reused across {owner} and {case_id}"
            )
        acceptance_source_owner[source_id] = case_id

        _, merged_dt = _timestamp(settlement_work["merged_at"], f"settlement {case_id}.work.merged_at")
        _, observed_dt = _timestamp(source["observed_at"], f"{name}.source.observed_at")
        if observed_dt < merged_dt:
            raise AcceptedWorkBridgeError(
                f"{name}.source predates merged_at; pre-merge evidence cannot prove accepted merged work"
            )
        if observed_dt > manifest_dt:
            raise AcceptedWorkBridgeError(f"{name}.source postdates acceptance.generated_at")

        accepted[case_id] = {
            "source": source,
            "source_trusted": trusted_sources.get(source_id) == source,
            "source_age_seconds": _age(observed_dt, as_of_dt, f"{name}.source.observed_at"),
        }
        accepted[case_id]["source_current"] = (
            accepted[case_id]["source_age_seconds"] <= freshness_seconds
        )
    return accepted, manifest_at


def _finish_state(
    queue_state: str,
    acceptance: Optional[dict[str, Any]],
    *,
    registry_current: bool,
) -> tuple[str, list[str]]:
    if queue_state == "SETTLED":
        return "PAID_CLOSED", ["CERTIFIED_SETTLEMENT_PAID"]
    if queue_state == "CLOSED_NO_REWARD":
        return "CLOSED_NO_REWARD", ["CERTIFIED_SETTLEMENT_CLOSED_NO_REWARD"]
    if acceptance is None:
        return "MERGED_WITHOUT_ACCEPTANCE_EVIDENCE", ["WORK_ACCEPTANCE_NOT_EVIDENCED"]
    if not acceptance["source_trusted"]:
        return "ACCEPTANCE_NEEDS_TRUST_EVIDENCE", ["WORK_ACCEPTANCE_SOURCE_NOT_TRUSTED"]
    if not registry_current or not acceptance["source_current"]:
        reasons: list[str] = []
        if not registry_current:
            reasons.append("TRUST_REGISTRY_STALE_AT_AS_OF")
        if not acceptance["source_current"]:
            reasons.append("WORK_ACCEPTANCE_EVIDENCE_STALE_AT_AS_OF")
        return "ACCEPTANCE_NEEDS_REFRESH", reasons
    if queue_state in _COLLECTION_CANDIDATES:
        return "ACCEPTED_UNPAID_COLLECTIONS_REVIEW", [
            "WORK_ACCEPTED_SOURCE_CERTIFIED",
            "CERTIFIED_SETTLEMENT_COLLECTION_CANDIDATE",
        ]
    if queue_state == "NEEDS_TRUST_EVIDENCE":
        return "ACCEPTED_UNPAID_SETTLEMENT_EVIDENCE_REVIEW", [
            "WORK_ACCEPTED_SOURCE_CERTIFIED",
            "SETTLEMENT_NEEDS_TRUST_EVIDENCE",
        ]
    if queue_state == "HOLD_CONTRADICTION":
        return "ACCEPTED_UNPAID_HOLD", [
            "WORK_ACCEPTED_SOURCE_CERTIFIED",
            "SETTLEMENT_HOLD_CONTRADICTION",
        ]
    raise AcceptedWorkBridgeError(f"unsupported settlement queue_state: {queue_state}")


def build_bridge(
    document: dict[str, Any],
    registry: dict[str, Any],
    key: bytes,
    acceptance_manifest: dict[str, Any],
    *,
    as_of: str,
    freshness_seconds: int,
) -> dict[str, Any]:
    """Build deterministic owner-review states without widening settlement authority."""
    if type(freshness_seconds) is not int or isinstance(freshness_seconds, bool):
        raise AcceptedWorkBridgeError("freshness_seconds must be an integer")
    if not 1 <= freshness_seconds <= MAX_FRESHNESS_SECONDS:
        raise AcceptedWorkBridgeError("freshness_seconds is outside supported bounds")
    as_of, as_of_dt = _timestamp(as_of, "as_of")

    try:
        queue = build_queue(
            document,
            registry,
            key,
            as_of=as_of,
            freshness_seconds=freshness_seconds,
        )
        trusted_sources, registry_body_sha = verify_registry(registry, key)
    except (CertificationError, ValueError) as exc:
        raise AcceptedWorkBridgeError(f"upstream settlement certification failed: {exc}") from exc

    registry_at, registry_dt = _timestamp(registry["generated_at"], "registry.generated_at")
    registry_current = _age(registry_dt, as_of_dt, "registry.generated_at") <= freshness_seconds
    accepted, acceptance_generated_at = _acceptance_records(
        document,
        acceptance_manifest,
        trusted_sources,
        as_of_dt=as_of_dt,
        registry_generated_dt=registry_dt,
        freshness_seconds=freshness_seconds,
    )
    work_by_case = {
        case["case_id"]: {
            "repo": case["work"]["repo"],
            "pr": case["work"]["pr"],
            "merge_commit_sha": case["work"]["merge_commit_sha"],
        }
        for case in document["cases"]
    }

    records: list[dict[str, Any]] = []
    for queue_row in queue["records"]:
        case_id = queue_row["case_id"]
        acceptance = accepted.get(case_id)
        finish_state, finish_reasons = _finish_state(
            queue_row["queue_state"], acceptance, registry_current=registry_current
        )
        if acceptance is None:
            acceptance_view = {
                "status": "ACCEPTANCE_NOT_EVIDENCED",
                "source_id": None,
                "source_ref": None,
                "source_sha256": None,
                "observed_at": None,
                "authority": None,
                "age_seconds": None,
                "trusted": False,
                "current": False,
            }
        else:
            src = acceptance["source"]
            trusted = acceptance["source_trusted"]
            current = acceptance["source_current"] and registry_current
            acceptance_view = {
                "status": (
                    "WORK_ACCEPTED_SOURCE_CERTIFIED"
                    if trusted and current
                    else (
                        "WORK_ACCEPTANCE_SOURCE_UNTRUSTED"
                        if not trusted
                        else "WORK_ACCEPTANCE_SOURCE_STALE"
                    )
                ),
                "source_id": src["source_id"],
                "source_ref": src["source_ref"],
                "source_sha256": src["source_sha256"],
                "observed_at": src["observed_at"],
                "authority": src["authority"],
                "age_seconds": acceptance["source_age_seconds"],
                "trusted": trusted,
                "current": current,
            }
        records.append({
            "case_id": case_id,
            "work": work_by_case[case_id],
            "settlement_queue_state": queue_row["queue_state"],
            "settlement_reason_codes": list(queue_row["reason_codes"]),
            "settlement_money": queue_row["money"],
            "work_acceptance": acceptance_view,
            "finish_state": finish_state,
            "finish_reason_codes": finish_reasons,
            "owner_review_only": True,
            "truth": {
                "merge_proves_acceptance": False,
                "acceptance_proves_award": False,
                "acceptance_proves_payment": False,
                "settlement_queue_grants_send_authority": False,
                "recognized_revenue": False,
            },
        })

    records.sort(key=lambda row: (row["work"]["repo"], row["work"]["pr"], row["case_id"]))
    counts: dict[str, int] = {}
    for row in records:
        counts[row["finish_state"]] = counts.get(row["finish_state"], 0) + 1

    body = {
        "schema": BRIDGE_SCHEMA,
        "as_of": as_of,
        "freshness_seconds": freshness_seconds,
        "acceptance_manifest_generated_at": acceptance_generated_at,
        "trusted_registry_generated_at": registry_at,
        "trusted_registry_body_sha256": registry_body_sha,
        "trusted_registry_current": registry_current,
        "settlement_queue_receipt_sha256": queue["receipt_sha256"],
        "records": records,
        "aggregates": {
            "case_count": len(records),
            "finish_state_counts": dict(sorted(counts.items())),
            "accepted_unpaid_review_count": sum(
                1 for row in records if row["finish_state"].startswith("ACCEPTED_UNPAID_")
            ),
        },
        "authority": {
            "send_outbound": False,
            "request_payout": False,
            "mutate_provider": False,
            "mutate_wallet_or_bank": False,
            "claim_payment": False,
            "recognize_accounting_revenue": False,
        },
    }
    return {**body, "receipt_sha256": hashlib.sha256(canonical(body)).hexdigest()}


def bridge_bytes(
    document: dict[str, Any],
    registry: dict[str, Any],
    key: bytes,
    acceptance_manifest: dict[str, Any],
    *,
    as_of: str,
    freshness_seconds: int,
) -> bytes:
    return canonical(
        build_bridge(
            document,
            registry,
            key,
            acceptance_manifest,
            as_of=as_of,
            freshness_seconds=freshness_seconds,
        )
    ) + b"\n"


def compile_bytes(
    document_raw: bytes,
    registry_raw: bytes,
    acceptance_raw: bytes,
    key: bytes,
    *,
    as_of: str,
    freshness_seconds: int,
) -> bytes:
    try:
        document = loads_strict(document_raw)
        registry = loads_strict(registry_raw)
        acceptance = loads_strict(acceptance_raw)
    except CertificationError as exc:
        raise AcceptedWorkBridgeError(f"strict input parsing failed: {exc}") from exc
    return bridge_bytes(
        document,
        registry,
        key,
        acceptance,
        as_of=as_of,
        freshness_seconds=freshness_seconds,
    )


def verify_bridge(
    document: dict[str, Any],
    registry: dict[str, Any],
    key: bytes,
    acceptance_manifest: dict[str, Any],
    bridge: dict[str, Any],
    *,
    as_of: str,
    freshness_seconds: int,
) -> bool:
    try:
        rebuilt = build_bridge(
            document,
            registry,
            key,
            acceptance_manifest,
            as_of=as_of,
            freshness_seconds=freshness_seconds,
        )
    except AcceptedWorkBridgeError:
        return False
    return hmac.compare_digest(canonical(rebuilt), canonical(bridge))


def render_markdown(bridge: dict[str, Any]) -> str:
    lines = [
        "# Accepted-work settlement review",
        "",
        f"As of: `{bridge['as_of']}`",
        "",
        "> Explicit acceptance is not an award, transfer, payment, or accounting revenue.",
        "",
        "## Cases",
        "",
    ]
    for row in bridge["records"]:
        work = row["work"]
        lines.extend([
            f"### {row['case_id']} — {work['repo']}#{work['pr']}",
            "",
            f"- Finish state: `{row['finish_state']}`",
            f"- Settlement queue: `{row['settlement_queue_state']}`",
            f"- Work acceptance: `{row['work_acceptance']['status']}`",
            "- Owner review only: `true`",
            "",
        ])
    lines.extend([
        "## Authority ceiling",
        "",
        "No outbound send, payout request, provider/wallet/bank mutation, payment claim, or accounting revenue recognition is authorized.",
        "",
    ])
    return "\n".join(lines)
