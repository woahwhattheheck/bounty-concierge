"""Offline owner bounty policy using the maintained canonical registry snapshot.

No caller-supplied allow flag, registry path, or replacement policy is accepted.
Keep the adjacent checker and registry synchronized with Commons docs/policies.
This gate supplies trust only; existing packet/payment/deduplication rules remain.
"""
from datetime import date
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from .bounty_payer_checker import POLICY_ID, exact, trust_decision, validate_registry


def collection_trust(repo, sponsor, task_ref, *, today=None):
    result = {"policy_id": POLICY_ID, "allow": False, "reason": "Trust evidence unavailable"}
    try:
        raw = Path(__file__).with_name("maintainer-credibility-registry.json").read_bytes()
        registry = json.loads(raw)
        validate_registry(registry)
        result["registry_sha256"] = hashlib.sha256(raw).hexdigest()
        candidates = []
        for row in registry["payers"] + registry["established_repository_exceptions"]:
            payer = row.get("identity", row.get("payer"))
            if exact(row.get("repository"), repo) and exact(payer, sponsor):
                candidates.append((payer, row.get("platform"), None))
        for row in registry["continued_work_exceptions"]:
            if exact(row.get("repository"), repo) and exact(row.get("merged_pr_ref"), task_ref):
                candidates.append((sponsor, "existing owner contribution", row.get("owner_contribution_author")))
        # An empty candidate still exercises the mandatory owner exclusion.
        if not candidates:
            candidates = [(sponsor, "unverified", None)]
        allowed = []
        for payer, platform, claimant in candidates:
            args = SimpleNamespace(repository=repo, payer=payer, platform=platform,
                operation="collect", task_ref=task_ref, claimant=claimant, amount=None,
                currency=None, task_evidence_ref=None, eligibility_evidence_ref=None,
                payment_rail=None, payment_rail_evidence_ref=None)
            decision, reason = trust_decision(registry, args, today or date.today())
            if decision == "ALLOW":
                allowed.append(reason)
            else:
                result["reason"] = reason
        # Multiple competing scopes never silently select one as authoritative.
        if len(candidates) == 1 and len(allowed) == 1:
            result.update(allow=True, reason=allowed[0])
        elif len(candidates) > 1:
            result["reason"] = "Ambiguous maintained payer/platform scope; no collection authority"
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return result
