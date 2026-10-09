# SPDX-License-Identifier: MIT
"""Compile one validated paid bounty into a deterministic swarm execution packet.

The MOVA factory is deliberately offline: it consumes already-collected canonical
and lease evidence and produces role-bound work orders. It performs no provider
reads or writes, creates no claims, and never changes payout state.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import re
import sys
from typing import Any


SCHEMA = "mova-bounty-factory/v1"
BATCH_SCHEMA = "mova-paid-bounty-wave/v1"
LEASE_SCHEMA = "bounty-work-order-lease/v1"
QA_EVIDENCE_SCHEMA = "mova-independent-qa/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FORBIDDEN_CLAIM_PHRASES = (
    "not claiming",
    "not a claim",
    "i am not claiming",
    "no claim",
    "do not claim",
    "don't claim",
    "not requesting",
    "do not request",
    "not seeking",
    "not asking for",
    "no compensation",
    "no payment",
    "no reward",
    "no payout",
    "without compensation",
    "without payment",
    "waive compensation",
    "waive payment",
    "waiver of compensation",
    "waiver of payment",
    "decline payment",
    "decline reward",
    "forfeit",
)
# A paid-work packet must positively request money, not merely mark a Boolean.
# Standalone /claim is an explicit portal claim; otherwise require both an
# affirmative action verb and a monetary reward reference in the claim text.
_CLAIM_COMMAND = re.compile(r"(?<!\S)/claim\b")
_CLAIM_ACTION = re.compile(r"\b(?:claim(?:s|ed|ing)?|request(?:s|ed|ing)?|seek(?:s|ing)?|ask(?:s|ed|ing)?|demand(?:s|ed|ing)?)\b")
_CLAIM_REWARD = re.compile(r"\b(?:bounty|reward|compensation|payment|payout|prize|allocation)\b")
_ROLE_ORDER = ("SCOUT", "BUILD", "QA", "PUBLISH", "COLLECT")
# Only already-authorized fleet GitHub actors may own paid source or payout lanes.
# Preserve original @tokenjunkielabs contributions when the author differs from
# the usual @woahwhattheheck claim/submission account.
_OWNER_GITHUB_ACTORS = frozenset(("tokenjunkielabs", "woahwhattheheck"))
_DEPENDENCIES = {
    "SCOUT": (),
    "BUILD": ("SCOUT_RECEIPT",),
    "QA": ("BUILD_RECEIPT",),
    "PUBLISH": ("QA_ACCEPT_RECEIPT",),
    "COLLECT": ("PUBLICATION_RECEIPT",),
}


class MovaFactoryError(ValueError):
    """The supplied candidate cannot safely enter the MOVA execution factory."""


def _text(value: Any, name: str, *, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\0" in value
        or len(value.encode("utf-8")) > maximum
    ):
        raise MovaFactoryError(f"{name} must be a bounded non-empty string")
    return value


def _sha256(value: Any, name: str) -> str:
    value = _text(value, name, maximum=64).lower()
    if _SHA256.fullmatch(value) is None:
        raise MovaFactoryError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def _git_sha_or_none(value: Any, name: str) -> str | None:
    if value is None:
        return None
    value = _text(value, name, maximum=40).lower()
    if _GIT_SHA.fullmatch(value) is None:
        raise MovaFactoryError(f"{name} must be a 40-character Git SHA")
    return value


def _canonical_hash(value: dict[str, Any]) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


_ELIGIBILITY_POLICY_ID = "REPO-ELIGIBILITY-20261009-BRYCE-01"


def _policy_time(value: Any, field: str) -> datetime:
    raw = _text(value, field, maximum=64)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MovaFactoryError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MovaFactoryError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _https_url(value: Any, field: str, *, github_pr: bool = False) -> str:
    raw = _text(value, field, maximum=2048)
    parsed = urlsplit(raw)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment):
        raise MovaFactoryError(f"{field} needs a canonical HTTPS evidence URL")
    if github_pr and (
        parsed.hostname != "github.com"
        or re.fullmatch(r"/[^/]+/[^/]+/pull/[1-9][0-9]*", parsed.path) is None
        or parsed.query
    ):
        raise MovaFactoryError(f"{field} needs a canonical merged GitHub PR URL")
    return raw


def _qualified_paid_repository(repo: str, policy: Any) -> dict[str, Any]:
    """Require a *separately verified* owner-controlled sponsor policy snapshot.

    This checks policy consistency/freshness, not the remote payment itself.
    Preserve original already-published PRs through their existing claim lane.
    """
    if (not isinstance(policy, dict)
            or policy.get("schema_version") != 1
            or policy.get("policy_id") != _ELIGIBILITY_POLICY_ID
            or policy.get("default_status") != "HOLD_UNVERIFIED"):
        raise MovaFactoryError("verified repository eligibility policy is required")
    days = policy.get("activity_max_age_days")
    if type(days) is not int or not 1 <= days <= 90:
        raise MovaFactoryError("repository policy activity window is invalid")
    now = datetime.now(timezone.utc)
    observed = _policy_time(policy.get("observed_at"), "policy.observed_at")
    age = now - observed
    if age < timedelta(minutes=-5) or age > timedelta(hours=24):
        raise MovaFactoryError("repository eligibility policy is stale or future dated")
    records = policy.get("repositories")
    if not isinstance(records, dict):
        raise MovaFactoryError("repository policy entries are missing")
    matches = [(key, value) for key, value in records.items()
               if isinstance(key, str) and key.casefold() == repo.casefold()]
    if len(matches) != 1:
        raise MovaFactoryError("repository is absent or ambiguous in paid eligibility policy")
    record = matches[0][1]
    if not isinstance(record, dict) or record.get("status") != "QUALIFIED_ACTIVE_PAID":
        raise MovaFactoryError("repository is not QUALIFIED_ACTIVE_PAID")
    activity = record.get("maintainer_activity")
    if not isinstance(activity, dict) or activity.get("kind") not in {"merge", "review", "response"}:
        raise MovaFactoryError("qualifying maintainer activity is missing")
    _text(activity.get("actor"), "maintainer_activity.actor", maximum=128)
    activity_url = _https_url(activity.get("evidence_url"), "maintainer_activity.evidence_url")
    expected_prefix = f"https://github.com/{repo}/".casefold()
    if not activity_url.casefold().startswith(expected_prefix):
        raise MovaFactoryError("maintainer activity is not in the candidate repository")
    activity_at = _policy_time(activity.get("event_at"), "maintainer_activity.event_at")
    if not timedelta(minutes=-5) <= now - activity_at <= timedelta(days=days):
        raise MovaFactoryError("maintainer activity is outside the allowed window")
    receipts = record.get("paid_merge_history")
    if not isinstance(receipts, list) or not receipts:
        raise MovaFactoryError("historical completed same-payer merge payment is missing")
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise MovaFactoryError("historical paid-merge record is malformed")
        _text(receipt.get("payer"), "paid_merge_history.payer", maximum=128)
        _text(receipt.get("recipient"), "paid_merge_history.recipient", maximum=128)
        _text(receipt.get("currency"), "paid_merge_history.currency", maximum=16)
        amount_raw = _text(str(receipt.get("amount")), "paid_merge_history.amount", maximum=64)
        try:
            amount = Decimal(amount_raw)
        except InvalidOperation as exc:
            raise MovaFactoryError("historical paid-merge amount is invalid") from exc
        if not amount.is_finite() or amount <= 0:
            raise MovaFactoryError("historical payment amount must be positive")
        _https_url(receipt.get("merged_pr_url"), "paid_merge_history.merged_pr_url", github_pr=True)
        _https_url(receipt.get("payment_evidence_url"), "paid_merge_history.payment_evidence_url")
        paid_at = _policy_time(receipt.get("paid_at"), "paid_merge_history.paid_at")
        if paid_at > now + timedelta(minutes=5):
            raise MovaFactoryError("historical payment date is in the future")
    return {
        "policy_id": _ELIGIBILITY_POLICY_ID,
        "policy_snapshot_sha256": _canonical_hash(policy),
        "status": "QUALIFIED_ACTIVE_PAID",
        "observed_at": observed.isoformat(),
        "maintainer_evidence_url": activity_url,
        "paid_merge_receipt_count": len(receipts),
        "provider_verification": "operator-supplied-owner-policy",
    }

# Cap currency values before any fixed-point formatting. A short input such as
# "1e100000" is finite but would otherwise expand into a 100,001-digit packet.
_MAX_REWARD_NUMERIC_CHARS = 64
_MAX_REWARD_USD = Decimal("1000000000000")


def _bounded_reward_amount(value: Decimal, name: str) -> Decimal:
    if not value.is_finite():
        raise MovaFactoryError(f"{name} must be finite")
    if len(value.as_tuple().digits) > _MAX_REWARD_NUMERIC_CHARS or value > _MAX_REWARD_USD:
        raise MovaFactoryError(f"{name} exceeds bounded USD magnitude")
    return value


def _enforce_minimum_reward_floor(value: Any) -> Decimal:
    """Callers may raise, but cannot lower, the USD $15 active-work floor."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise MovaFactoryError("minimum_reward_usd must be a finite Decimal")
    _bounded_reward_amount(value, "minimum_reward_usd")
    if value < Decimal("15"):
        raise MovaFactoryError("minimum_reward_usd must be at least $15")
    return value


def _reward(value: Any, minimum_reward_usd: Decimal) -> str:
    minimum_reward_usd = _enforce_minimum_reward_floor(minimum_reward_usd)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise MovaFactoryError("reward_usd must be numeric")
    try:
        literal = str(value)
        if len(literal) > _MAX_REWARD_NUMERIC_CHARS:
            raise MovaFactoryError("reward_usd exceeds bounded numeric input")
        reward = Decimal(literal)
    except (InvalidOperation, ValueError) as exc:
        raise MovaFactoryError("reward_usd must be numeric") from exc
    _bounded_reward_amount(reward, "reward_usd")
    if reward < minimum_reward_usd:
        raise MovaFactoryError(
            f"reward_usd must meet the ${minimum_reward_usd} active-work floor"
        )
    result = format(reward.normalize(), "f")
    if len(result) > _MAX_REWARD_NUMERIC_CHARS:
        raise MovaFactoryError("reward_usd exceeds bounded numeric output")
    return result


def _lease(value: Any, repo: str, issue_number: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MovaFactoryError("lease_receipt must be an object")
    if (
        value.get("schema") != LEASE_SCHEMA
        or value.get("status") != "READY"
        or value.get("dispatch") is not True
        or value.get("repo") != repo
        or value.get("number") != issue_number
    ):
        raise MovaFactoryError("lease_receipt is not READY for this exact target")
    original = value.get("original")
    refreshed = value.get("refreshed")
    if not isinstance(original, dict) or not isinstance(refreshed, dict):
        raise MovaFactoryError("lease_receipt is missing generation evidence")
    original_generation = _sha256(
        original.get("source_generation_sha256"),
        "lease original source_generation_sha256",
    )
    refreshed_generation = _sha256(
        refreshed.get("source_generation_sha256"),
        "lease refreshed source_generation_sha256",
    )
    if original_generation != refreshed_generation:
        raise MovaFactoryError("READY lease source generation drift")
    return {
        "schema": LEASE_SCHEMA,
        "status": "READY",
        "dispatch": True,
        "repo": repo,
        "number": issue_number,
        "original_capture_receipt_sha256": _sha256(
            original.get("capture_receipt_sha256"),
            "lease original capture_receipt_sha256",
        ),
        "refreshed_capture_receipt_sha256": _sha256(
            refreshed.get("capture_receipt_sha256"),
            "lease refreshed capture_receipt_sha256",
        ),
        "source_generation_sha256": refreshed_generation,
    }


def _owner_account(value: Any, field: str) -> str:
    account = _text(value, field, maximum=128)
    if account not in _OWNER_GITHUB_ACTORS:
        raise MovaFactoryError(
            f"{field} must be an authorized original-contributor GitHub actor"
        )
    return account


def _owners(value: Any) -> dict[str, str]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise MovaFactoryError("owners must be an object")
    unknown = set(value) - set(_ROLE_ORDER)
    if unknown:
        raise MovaFactoryError(f"unknown owner roles: {','.join(sorted(unknown))}")
    return {
        role: _text(value.get(role, "UNASSIGNED"), f"owners.{role}", maximum=256)
        for role in _ROLE_ORDER
    }


def _claim(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MovaFactoryError("compensation_claim must be an object")
    required = value.get("required")
    text = value.get("text")
    if type(required) is not bool:
        raise MovaFactoryError("compensation_claim.required must be boolean")
    if not required:
        raise MovaFactoryError("paid candidate requires an affirmative compensation claim")
    text = _text(text, "compensation_claim.text", maximum=4096)
    folded = " ".join(text.casefold().split())
    if any(phrase in folded for phrase in _FORBIDDEN_CLAIM_PHRASES):
        raise MovaFactoryError("compensation_claim.text contains waiver language")
    if not (
        _CLAIM_COMMAND.search(folded)
        or (_CLAIM_ACTION.search(folded) and _CLAIM_REWARD.search(folded))
    ):
        raise MovaFactoryError(
            "compensation_claim.text requires an affirmative bounty or payment claim"
        )
    return {"required": True, "text": text}


def _constraints(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 64:
        raise MovaFactoryError("constraints must be a list with at most 64 entries")
    return sorted(set(_text(item, "constraint", maximum=1024) for item in value))


def _optional_qa_handoff(value: Any, issue_url: str, capture: str) -> dict[str, Any] | None:
    """Remove only a separate QA seat, never the builder's focused acceptance checks."""
    if value is None:
        return None
    fields = {
        "schema", "separate_qa_required", "issue_url", "capture_sha256",
        "checked_at", "builder_focused_check",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise MovaFactoryError("qa_handoff requires exact evidence fields")
    if (
        value["schema"] != QA_EVIDENCE_SCHEMA
        or value["separate_qa_required"] is not False
        or value["builder_focused_check"] is not True
        or value["issue_url"] != issue_url
        or _sha256(value["capture_sha256"], "qa_handoff.capture_sha256") != capture
    ):
        raise MovaFactoryError("independent QA waiver lacks canonical, focused-check evidence")
    raw = value["checked_at"]
    if not isinstance(raw, str) or not 20 <= len(raw) <= 64 or raw != raw.strip():
        raise MovaFactoryError("qa_handoff.checked_at must be a bounded timestamp")
    try:
        instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MovaFactoryError("qa_handoff.checked_at is malformed") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise MovaFactoryError("qa_handoff.checked_at must be timezone aware")
    elapsed = datetime.now(timezone.utc) - instant.astimezone(timezone.utc)
    if elapsed < timedelta(0) or elapsed > timedelta(hours=6):
        raise MovaFactoryError("independent QA waiver requires a fresh source capture")
    return dict(value)


def compile_mova_packet(
    candidate: dict[str, Any],
    *,
    owners: dict[str, str] | None = None,
    minimum_reward_usd: Decimal = Decimal("15"),
    repo_eligibility_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a canonical paid candidate into collision-safe role work orders."""
    if not isinstance(candidate, dict):
        raise MovaFactoryError("candidate must be an object")
    if candidate.get("canonical_open") is not True:
        raise MovaFactoryError("candidate must be canonically open")
    if candidate.get("platform_green") is not True:
        raise MovaFactoryError("candidate platform is not green for paid work")
    # An open issue and a bounty label are not publishability evidence: archived
    # sponsor repositories reject new pull requests even when issues stay open.
    # Intake must read this directly from the canonical repository metadata.
    if candidate.get("repository_archived") is not False:
        raise MovaFactoryError(
            "sponsor repository must be provider-verified as unarchived"
        )

    repo = _text(candidate.get("repo"), "repo", maximum=256)
    if _REPO.fullmatch(repo) is None:
        raise MovaFactoryError("repo must be owner/name")
    qualified_repo = _qualified_paid_repository(repo, repo_eligibility_policy)
    issue_number = candidate.get("issue_number")
    if isinstance(issue_number, bool) or not isinstance(issue_number, int) or issue_number <= 0:
        raise MovaFactoryError("issue_number must be a positive integer")

    canonical_issue_url = _text(candidate.get("canonical_issue_url"), "canonical_issue_url")
    expected_url = f"https://github.com/{repo}/issues/{issue_number}"
    if canonical_issue_url != expected_url:
        raise MovaFactoryError("canonical_issue_url does not match repo/issue_number")

    platform = _text(candidate.get("platform"), "platform", maximum=128)
    reward_usd = _reward(candidate.get("reward_usd"), minimum_reward_usd)
    capture_sha = _sha256(
        candidate.get("canonical_capture_sha256"), "canonical_capture_sha256"
    )
    qa_evidence = _optional_qa_handoff(
        candidate.get("qa_handoff"), canonical_issue_url, capture_sha,
    )
    generation_sha = _sha256(
        candidate.get("source_generation_sha256"), "source_generation_sha256"
    )
    expected_head = _git_sha_or_none(candidate.get("expected_head"), "expected_head")
    lease = _lease(candidate.get("lease_receipt"), repo, issue_number)
    if capture_sha != lease["original_capture_receipt_sha256"]:
        raise MovaFactoryError("candidate canonical capture disagrees with READY lease")
    if generation_sha != lease["source_generation_sha256"]:
        raise MovaFactoryError("candidate source generation disagrees with READY lease")

    claim = _claim(candidate.get("compensation_claim"))
    role_owners = _owners(owners)
    constraints = _constraints(candidate.get("constraints"))
    work_account = _owner_account(
        candidate.get("work_account", "tokenjunkielabs"), "work_account"
    )
    submission_account = _owner_account(
        candidate.get("submission_account", "woahwhattheheck"),
        "submission_account",
    )

    operation_material = {
        "platform": platform.casefold(),
        "repo": repo.casefold(),
        "issue_number": issue_number,
        "capture_sha256": capture_sha,
        "source_generation_sha256": generation_sha,
    }
    operation_digest = _canonical_hash(operation_material)
    operation_id = f"MOVA-{operation_digest[:20].upper()}"
    target_key = f"{repo.casefold()}#{issue_number}"

    if qa_evidence is not None and role_owners["QA"] != "UNASSIGNED":
        raise MovaFactoryError("cannot drop an assigned QA handoff")
    role_packets: list[dict[str, Any]] = []
    for role in _ROLE_ORDER:
        if role == "QA" and qa_evidence is not None:
            continue
        account = submission_account if role in {"PUBLISH", "COLLECT"} else work_account
        role_packets.append(
            {
                "role": role,
                "owner": role_owners[role],
                "account": account,
                "lease_key": f"{operation_id}:{role}",
                "target_key": target_key,
                "depends_on": (
                    ["BUILD_RECEIPT"] if role == "PUBLISH" and qa_evidence is not None
                    else list(_DEPENDENCIES[role])
                ),
                "must_preserve": [
                    "canonical_issue_identity",
                    "source_generation",
                    "original_contributor_credit",
                    "compensation_claim",
                ],
                "may_mutate_provider": role in {"PUBLISH", "COLLECT"},
                "handoff_receipt": f"{role}_RECEIPT",
            }
        )

    core = {
        "schema": SCHEMA,
        "operation_id": operation_id,
        "target": {
            "repo": repo,
            "issue_number": issue_number,
            "canonical_issue_url": canonical_issue_url,
            "target_key": target_key,
            "repository_archived": False,
            "expected_head": expected_head,
        },
        "economics": {
            "platform": platform,
            "reward_usd": reward_usd,
            "minimum_reward_usd": format(minimum_reward_usd.normalize(), "f"),
            "platform_green": True,
            "compensation_claim": claim,
        },
        "identity": {
            "work_account": work_account,
            "submission_account": submission_account,
        },
        "evidence": {
            "repo_paid_eligibility": qualified_repo,
            "canonical_capture_sha256": capture_sha,
            "source_generation_sha256": generation_sha,
            "lease": lease,
        },
        "constraints": constraints,
        "roles": role_packets,
        "coordination": {
            "parallelism": "pipeline",
            "one_owner_per_role": True,
            "one_active_source_builder": True,
            "publish_exact_accepted_head": True,
            "collection_requires_publication_receipt": True,
            "recheck_before_source_mutation": True,
            "recheck_before_provider_mutation": True,
        },
        "authority": {
            "artifact_compilation_only": True,
            "provider_reads": False,
            "provider_writes": False,
            "source_writes": False,
            "claim_writes": False,
            "payment_writes": False,
        },
    }
    if qa_evidence is not None:
        core["evidence"]["qa_handoff"] = qa_evidence
        core["coordination"]["independent_qa_handoff"] = "NOT_REQUIRED_WITH_SOURCE_EVIDENCE"
        core["coordination"]["builder_focused_check_still_required"] = True
    packet = dict(core)
    packet["packet_sha256"] = _canonical_hash(core)
    return packet


def compile_mova_batch(
    candidates: list[dict[str, Any]],
    *,
    owners: dict[str, dict[str, str]] | None = None,
    minimum_reward_usd: Decimal = Decimal("15"),
    repo_eligibility_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a bounded wave without permitting conflicting claims on a target.

    Identical repeated candidates are idempotent. Two different READY snapshots
    for one repo/issue are a collision, not permission for two parallel builders.
    This offline manifest does not claim a lease or publish to the provider.
    """
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 128:
        raise MovaFactoryError("batch must contain 1 to 128 paid candidates")
    if owners is None:
        owners = {}
    if not isinstance(owners, dict):
        raise MovaFactoryError("batch owners must be a target-keyed object")
    packets: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise MovaFactoryError("batch candidate must be an object")
        repo = candidate.get("repo")
        issue = candidate.get("issue_number")
        if not isinstance(repo, str) or _REPO.fullmatch(repo) is None:
            raise MovaFactoryError("batch candidate repo must be owner/name")
        if type(issue) is not int or issue <= 0:
            raise MovaFactoryError("batch issue number must be positive")
        target_key = f"{repo.casefold()}#{issue}"
        packet = compile_mova_packet(
            candidate, owners=owners.get(target_key),
            minimum_reward_usd=minimum_reward_usd,
            repo_eligibility_policy=repo_eligibility_policy,
        )
        previous = packets.get(target_key)
        if previous is not None and previous["packet_sha256"] != packet["packet_sha256"]:
            raise MovaFactoryError(
                f"conflicting READY snapshots or owners for target {target_key}"
            )
        packets[target_key] = packet
    unused = set(owners) - set(packets)
    if unused:
        raise MovaFactoryError("batch owners contains unknown target keys")
    ordered = [packets[key] for key in sorted(packets)]
    core = {
        "schema": BATCH_SCHEMA,
        "count": len(ordered),
        "deduplicated_count": len(candidates) - len(ordered),
        "targets": [
            {
                "target_key": packet["target"]["target_key"],
                "operation_id": packet["operation_id"],
                "packet_sha256": packet["packet_sha256"],
            }
            for packet in ordered
        ],
        "packets": ordered,
        "authority": {
            "offline_compilation_only": True,
            "provider_reads": False,
            "provider_writes": False,
            "claims_submitted": False,
        },
    }
    return {**core, "batch_sha256": _canonical_hash(core)}


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MovaFactoryError("input is not valid UTF-8 JSON") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compile one validated bounty into a MOVA swarm execution packet."
    )
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--owners", type=Path)
    parser.add_argument("--batch", action="store_true", help="compile a list of paid candidates with collision checks")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--minimum-reward-usd", default="15")
    parser.add_argument("--repo-eligibility-policy", type=Path,
                        help="current owner-controlled qualified paid-repo JSON snapshot")
    args = parser.parse_args(argv)

    try:
        minimum = Decimal(args.minimum_reward_usd)
        if not minimum.is_finite() or minimum < 0:
            raise MovaFactoryError("minimum reward must be a non-negative number")
        policy = (_load(args.repo_eligibility_policy)
                  if args.repo_eligibility_policy is not None else None)
        owners = _load(args.owners) if args.owners is not None else None
        data = _load(args.candidate)
        packet = (
            compile_mova_batch(data, owners=owners, minimum_reward_usd=minimum,
                               repo_eligibility_policy=policy)
            if args.batch
            else compile_mova_packet(data, owners=owners, minimum_reward_usd=minimum,
                                     repo_eligibility_policy=policy)
        )
        rendered = json.dumps(packet, indent=2, sort_keys=True) + "\n"
        if args.output is None:
            sys.stdout.write(rendered)
        else:
            args.output.write_text(rendered, encoding="utf-8")
    except (MovaFactoryError, OSError, InvalidOperation):
        print("mova-factory: invalid candidate, owners, or output", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
