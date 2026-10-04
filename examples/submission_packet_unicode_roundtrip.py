# SPDX-License-Identifier: MIT
"""Offline packet -> custody -> transport example; no provider calls or sends.

Run from the source checkout with:
    PYTHONPATH=. python examples/submission_packet_unicode_roundtrip.py
"""

from copy import deepcopy
import hashlib
import json

from concierge.submission_custody import (
    SubmissionCustodyError, empty_ledger, register_candidate, verify_ledger,
)
from concierge.submission_packet import build_submission_packet
from concierge.submission_transport_router import (
    SubmissionTransportInputError, compile_transport_decision,
    compile_transport_operation, verify_transport_decision,
)

SOURCE = "https://github.com/example/project/issues/10"
WHEN = "2026-10-04T09:00:00Z"


def digest(value, *, ensure_ascii=False):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=ensure_ascii).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def packet_for(path="src/main.py", command="python -m pytest",
               criterion="native-path", excerpt=None):
    row = {
        "canonical_source_url": SOURCE, "advertised_reward_usd": "50",
        "authority": {"reward": "advertised_only",
                      "revenue": "not_earned_or_settled_by_this_receipt"},
    }
    if excerpt is not None:
        row["submission_target"] = {
            "repository": "example/project", "source_url": SOURCE,
            "source_content_sha256": "c" * 64, "instruction_excerpt": excerpt,
        }
    portfolio = {
        "schema": "qualified-opportunity-portfolio/v1",
        "authority": {"cash_claim": False}, "selected_count": 1, "selected": [row],
    }
    evidence = [{
        "canonical_source_url": SOURCE,
        "pull_request_url": "https://github.com/example/project/pull/15",
        "head_sha": "a" * 40, "changed_paths": [path], "allowed_paths": [path],
        "tests": [{"command": command, "outcome": "PASS"}],
        "evidence_sha256": "b" * 64,
        "acceptance_checks": [{"criterion_id": criterion, "status": "PASS"}],
    }]
    return build_submission_packet(portfolio, evidence)["packets"][0]


def observe(packet):
    row = {"packet_sha256": packet["packet_sha256"]}
    registration = dict(
        event_id="event-001", occurred_at=WHEN, submission_id="submission-001",
        packet=packet, artifact_revision=1, route_class="github_pr",
        route_key="original-pr", as_of=WHEN,
    )
    try:
        registered = register_candidate(empty_ledger(), **registration)
        replayed = register_candidate(registered["ledger"], **registration)
        verified = verify_ledger(registered["ledger"], as_of=WHEN)
        row.update(custody=registered["receipt"]["send_disposition"],
                   custody_receipt_sha256=registered["receipt"]["receipt_sha256"],
                   ledger_sha256=verified["ledger_sha256"],
                   replayed=replayed["replayed"])
    except SubmissionCustodyError as exc:
        row["custody"] = exc.code

    policy = {
        "schema": "sponsor-submission-transport-policy/v1",
        "policy_id": "example-policy", "canonical_source_url": SOURCE,
        "packet_sha256": packet["packet_sha256"], "policy_evidence_sha256": "d" * 64,
        "routes": [{"route_id": "original-pr", "route_class": "github-pr",
                    "authority_id": "example-adapter",
                    "route_evidence_sha256": "e" * 64, "failover_on": []}],
    }
    policy["policy_sha256"] = digest(policy)
    request = {"packet": packet, "policy": policy, "attempts": []}
    try:
        operation = compile_transport_operation(packet, policy, 0)
        decision = compile_transport_decision(request)
        row.update(transport=decision["disposition"],
                   operation_sha256=operation["operation_sha256"],
                   decision_sha256=decision["decision_sha256"],
                   decision_verified=verify_transport_decision(request, decision),
                   external_send_authorized=decision["authority"]["external_send_authorized"])
    except SubmissionTransportInputError as exc:
        row["transport"] = str(exc)
    return row


def main():
    wire_unicode = packet_for(path="src/café-📦.py")
    legacy_unicode = deepcopy(wire_unicode)
    legacy_unicode["packet_sha256"] = digest({
        key: value for key, value in legacy_unicode.items() if key != "packet_sha256"
    })
    cases = [
        ("ascii-wire", packet_for(), False),
        ("unicode-path-wire", wire_unicode, False),
        ("unicode-evidence-wire", packet_for(command="python -m vérification",
                                             criterion="résultat-✓"), False),
        ("unicode-target-wire", packet_for(excerpt="Livrer au dépôt indiqué 📦."), False),
        ("legacy-utf8-unicode", legacy_unicode, False),
    ]
    for name, packet in (("tampered-wire", wire_unicode),
                         ("tampered-legacy", legacy_unicode)):
        changed = deepcopy(packet)
        changed["advertised_reward_usd"] = "51"
        cases.append((name, changed, True))

    results = []
    for name, packet, tampered in cases:
        result = {"case": name, **observe(packet)}
        if tampered:
            passed = (result["custody"] == "PACKET_DIGEST_MISMATCH"
                      and "PACKET_DIGEST_MISMATCH" in result["transport"])
        else:
            passed = (result["custody"] == "READY_TO_SUBMIT"
                      and result["transport"] == "READY_PRIMARY"
                      and result.get("replayed") is True
                      and result.get("decision_verified") is True
                      and result.get("external_send_authorized") is False)
        result["passed"] = passed
        results.append(result)
    passed = sum(row["passed"] for row in results)
    print(json.dumps({"passed": passed, "total": len(results), "results": results},
                     indent=2, ensure_ascii=False))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
