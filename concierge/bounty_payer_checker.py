#!/usr/bin/env python3
"""Apply BOUNTY-TRUST-BAN-20261011 to every bounty operation (stdlib only)."""
from __future__ import annotations
import argparse
import json
import sys
from datetime import date
from pathlib import Path

DEFAULT_REGISTRY = Path(__file__).with_name("maintainer-credibility-registry.json")
POLICY_ID = "BOUNTY-TRUST-BAN-20261011"
ALLOWED = {"ALLOW": 0, "HOLD": 2, "BLOCK": 3}
BOUNTY_OPERATIONS = {
    "discover", "due-diligence", "research", "work", "revise", "submit", "claim",
    "collect", "follow-up", "queue", "retry", "monitor", "download", "install", "run",
}
EVIDENCE_OPERATIONS = {"security-evidence", "report", "block", "preserve"}
TASK_OPERATIONS = {"work", "revise", "submit", "claim", "download", "install", "run"}
CLAIM_OPERATIONS = {"collect", "follow-up", "queue", "retry", "monitor"}

def parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None

def age_ok(value, today, max_days=30):
    parsed = parse_date(value)
    return parsed is not None and 0 <= (today - parsed).days <= max_days

def present(value):
    return isinstance(value, str) and bool(value.strip())

def exact(value, expected):
    return present(value) and present(expected) and value.strip().casefold() == expected.strip().casefold()

def validate_registry(registry):
    if not isinstance(registry, dict):
        raise ValueError("Registry root must be an object.")
    if registry.get("policy_id") != POLICY_ID:
        raise ValueError("Registry must implement the current owner bounty trust ban.")
    for key in ("payers", "blacklist", "owner_overrides", "owner_exclusions", "established_repository_exceptions", "continued_work_exceptions"):
        if not isinstance(registry.get(key), list):
            raise ValueError(f"Registry field {key} must be a list.")
        if any(not isinstance(row, dict) for row in registry[key]):
            raise ValueError(f"Every {key} entry must be an object.")

def work_scope(args):
    return {
        "repository": args.repository,
        "payer": args.payer,
        "platform": args.platform,
        "task_ref": args.task_ref,
        "amount": args.amount,
        "currency": (args.currency or "").upper(),
        "task_evidence_ref": args.task_evidence_ref,
        "eligibility_evidence_ref": args.eligibility_evidence_ref,
        "payment_rail": args.payment_rail,
        "payment_rail_evidence_ref": args.payment_rail_evidence_ref,
    }

def assignment_decision(args):
    """Apply the owner's original assignment terms to the exact claimant."""
    required = getattr(args, "assignment_required", "unknown")
    evidence = getattr(args, "assignment_evidence_ref", None)
    verified = getattr(args, "assignment_verified", False)
    if required == "unknown":
        return "HOLD", "Assignment requirement is unknown; eligible work still needs original task terms."
    if not (verified and present(evidence)):
        return "HOLD", "Verified original assignment terms or provider assignment evidence are required."
    if required == "no":
        return None
    claimant = getattr(args, "claimant", None)
    assignee = getattr(args, "assignment_assignee", None)
    if not exact(assignee, claimant):
        return "HOLD", "Required provider/maintainer assignment must match the exact original claimant/payee."
    return None

def scope_matches(item, args, payer_key="payer"):
    return (exact(item.get(payer_key), args.payer) and
            exact(item.get("platform"), args.platform) and
            exact(item.get("repository"), args.repository))

def owner_excluded(registry, args):
    # The owner-named Mova exclusion cannot disappear through a stale registry.
    # Do not guess additional provider aliases or unrelated accounts.
    owner = args.repository.partition("/")[0]
    if any(exact(args.payer, alias) or exact(owner, alias) for alias in ("Mova", "Movalabs-crew")):
        return True
    for item in registry["owner_exclusions"]:
        if (any(exact(alias, args.payer) for alias in item.get("payer_aliases", [])) or
                any(exact(alias, owner) for alias in item.get("repository_owners", [])) or
                any(exact(repo, args.repository) for repo in item.get("repositories", []))):
            return True
    return False

def documented_block(registry, args):
    return any(
        item.get("documented") is True and
        item.get("bounty_payment_promised_or_owed") is True and
        item.get("accepted_or_merged") is True and
        item.get("ignored_requests_or_refused_or_default") is True and
        item.get("evidence_refs") and scope_matches(item, args)
        for item in registry["blacklist"]
    )

def current_override(registry, args, excluded):
    scope = work_scope(args)
    for override in registry["owner_overrides"]:
        if (override.get("authorized_by") == "Bryce" and
                present(override.get("instruction_ref")) and
                override.get("instruction_ref") != registry.get("owner_instruction_ref") and
                override.get("supersedes_policy_id") == POLICY_ID and
                args.operation in override.get("approved_operations", []) and
                (not excluded or override.get("explicitly_reinstates_excluded_scope") is True) and
                all(exact(override.get(k), v) for k, v in scope.items())):
            return True
    return False

def proven_payer(payer, args, today, max_days):
    if payer.get("status") != "GREEN":
        return False
    if not age_ok(payer.get("last_verified_at"), today, max_days):
        return False
    if not (age_ok(payer.get("recent_activity_at"), today, max_days) and
            present(payer.get("recent_activity_evidence_ref"))):
        return False
    return any(
        (exact(e.get("repository"), args.repository) or
         (payer.get("trust_basis") == "proven_maintainer" and
          payer.get("sponsor_scope_verified") is True and
          present(payer.get("sponsor_scope_evidence_ref")))) and
        e.get("paid_out") is True and e.get("accepted") is True and
        e.get("merged_upstream") is True and present(e.get("evidence_ref")) and
        (present(e.get("paid_at")) or present(e.get("payment_verified_at"))) and
        present(e.get("bounty_ref")) and present(e.get("pr_ref")) and present(e.get("contributor"))
        for e in payer.get("completed_payments", [])
    )

def established_repository(item, today, max_days):
    return (
        item.get("status") == "GREEN" and
        age_ok(item.get("last_verified_at"), today, max_days) and
        item.get("legitimate_repository") is True and
        item.get("reputable_maintainer") is True and
        present(item.get("reputation_evidence_ref")) and
        item.get("substantial_real_adoption") is True and
        isinstance(item.get("stars_observed"), int) and
        not isinstance(item.get("stars_observed"), bool) and item["stars_observed"] > 0 and
        present(item.get("adoption_evidence_ref")) and
        item.get("merged_owner_contribution") is True and
        item.get("claimant_verified_owner") is True and present(item.get("claimant")) and
        present(item.get("merged_pr_ref")) and present(item.get("merge_evidence_ref"))
    )

def trust_decision(registry, args, today):
    excluded = owner_excluded(registry, args) or documented_block(registry, args)
    if current_override(registry, args, excluded):
        return "ALLOW", "Explicit later Bryce instruction matches this policy and exact operation/task."
    if excluded:
        return "BLOCK", "Owner-excluded or documented blocked maintainer: all bounty work and collections stop."
    if args.operation in {"work", "revise", "submit", "collect", "follow-up", "queue", "retry", "monitor"}:
        for item in registry["continued_work_exceptions"]:
            if (item.get("status") == "CONTINUED_WORK_EXCEPTION" and
                    exact(item.get("repository"), args.repository) and
                    exact(item.get("merged_pr_ref"), args.task_ref) and
                    exact(item.get("owner_contribution_author"), args.claimant) and
                    present(item.get("merged_at")) and present(item.get("provider_readback"))):
                return "ALLOW", "Exact existing owner contribution in an established legitimate repository may continue; no new bounty is approved."
    matching = [p for p in registry["payers"] if scope_matches(p, args, "identity")]
    established = [p for p in registry["established_repository_exceptions"] if scope_matches(p, args)]
    records = matching + established
    if any(p.get("known_overdue_accepted_bounty") is True for p in records):
        return "BLOCK", "Unresolved overdue accepted bounty prevents a trust exception and all collections."
    if any(p.get("status") in {"BLOCK", "BLACKLISTED", "OWNER_EXCLUDED"} for p in records):
        return "BLOCK", "An exact-scope exclusion cannot be bypassed by another trust record."
    if len(matching) > 1 or len(established) > 1:
        return "BLOCK", "Conflicting duplicate exact-scope records; no trust exception established."
    max_days = int(registry.get("freshness", {}).get("max_days", 30))
    if any(proven_payer(p, args, today, max_days) for p in matching):
        return "ALLOW", "Documented exact-scope proven-payer exception."
    if any(established_repository(p, today, max_days) for p in established):
        return "ALLOW", "Documented established legitimate repository with our already-merged contribution."
    return "BLOCK", "No current documented trust exception: stop all bounty activity, including diligence and collections."

def decide(registry, args, today=None):
    today = today or date.today()
    if args.operation in EVIDENCE_OPERATIONS:
        return "ALLOW", "Only authorized factual reporting/blocking or preservation of security/history evidence; no bounty diligence, code execution, or collections."
    if args.operation not in BOUNTY_OPERATIONS:
        return "BLOCK", "Unsupported operation."
    validate_registry(registry)
    trust, reason = trust_decision(registry, args, today)
    if trust != "ALLOW":
        return trust, reason
    if args.operation in TASK_OPERATIONS:
        required = (args.repository, args.payer, args.platform, args.task_ref, args.amount, args.currency,
                    args.task_evidence_ref, args.eligibility_evidence_ref, args.payment_rail,
                    args.payment_rail_evidence_ref)
        if not all(present(value) for value in required):
            return "HOLD", "Eligible maintainer, but current task, amount, eligibility, payment rail, and evidence are required."
        if not (args.task_evidence_verified and args.eligibility_verified and args.payment_rail_verified):
            return "HOLD", "Task amount, eligibility, and payment rail must each be VERIFIED after source readback."
        assignment = assignment_decision(args)
        if assignment is not None:
            return assignment
    if args.operation in CLAIM_OPERATIONS and not present(args.task_ref):
        return "HOLD", "Eligible maintainer, but the original exact task/claim reference is required."
    return "ALLOW", reason + " Other owner, task, contact, validation, and cost restrictions still apply."

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    ap.add_argument("--repository", required=True)
    ap.add_argument("--payer", required=True, help="Exact maintainer/payer identity")
    ap.add_argument("--platform", required=True)
    ap.add_argument("--operation", required=True, choices=sorted(BOUNTY_OPERATIONS | EVIDENCE_OPERATIONS))
    ap.add_argument("--task-ref", help="Exact bounty/task or historical claim provider reference")
    ap.add_argument("--amount", help="Exact currently advertised/committed amount")
    ap.add_argument("--currency", help="Currency for --amount")
    ap.add_argument("--task-evidence-ref", help="Reference proving current task and amount")
    ap.add_argument("--task-evidence-verified", action="store_true", help="Set only after task amount readback")
    ap.add_argument("--eligibility-evidence-ref", help="Reference proving current eligibility terms")
    ap.add_argument("--eligibility-verified", action="store_true", help="Set only after eligibility readback")
    ap.add_argument("--payment-rail", help="Exact current payout rail")
    ap.add_argument("--payment-rail-evidence-ref", help="Reference proving that rail applies to this task")
    ap.add_argument("--payment-rail-verified", action="store_true", help="Set only after payment rail readback")
    ap.add_argument("--assignment-required", choices=("yes", "no", "unknown"), default="unknown")
    ap.add_argument("--assignment-evidence-ref", help="Original terms or provider/maintainer assignment reference")
    ap.add_argument("--assignment-verified", action="store_true", help="Set only after source readback, not a self-claim")
    ap.add_argument("--claimant", help="Exact original claimant/payee identity")
    ap.add_argument("--assignment-assignee", help="Exact provider/maintainer-issued assignee")
    args = ap.parse_args()
    try:
        if args.operation in EVIDENCE_OPERATIONS:
            decision, reason = decide({}, args)
        else:
            registry = json.loads(args.registry.read_text(encoding="utf-8"))
            decision, reason = decide(registry, args)
    except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError, KeyError) as exc:
        decision, reason = "BLOCK", f"Current trust registry unavailable or invalid; bounty activity stops: {exc}"
    print(json.dumps({"policy_id": POLICY_ID, "decision": decision, "reason": reason,
                      "repository": args.repository, "payer": args.payer,
                      "platform": args.platform, "operation": args.operation},
                     ensure_ascii=False, separators=(",", ":")))
    return ALLOWED[decision]

if __name__ == "__main__":
    sys.exit(main())

