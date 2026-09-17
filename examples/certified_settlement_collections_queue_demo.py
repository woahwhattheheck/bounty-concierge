#!/usr/bin/env python3
"""Provider-free synthetic demo for the certified settlement collections queue."""
from __future__ import annotations

import hashlib
import hmac

from concierge import reward_settlement_certifier as certifier
from concierge.certified_settlement_collections_queue import compile_queue

KEY = b"synthetic-demo-key-not-for-production"
GEN = "2026-09-16T12:00:00Z"


def source(source_id: str, authority: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "source_ref": f"synthetic:{source_id}",
        "source_sha256": hashlib.sha256(source_id.encode("utf-8")).hexdigest(),
        "observed_at": GEN,
        "authority": authority,
    }


def main() -> int:
    work_source = source("demo-work-source", "REPOSITORY")
    award_source = source("demo-award-source", "SPONSOR")
    document = {
        "schema": "bounty-concierge/reward-settlement-input/v1",
        "generated_at": GEN,
        "cases": [
            {
                "case_id": "demo-1",
                "work": {
                    "repo": "example/project",
                    "pr": 42,
                    "merge_commit_sha": "a" * 40,
                    "merged_at": "2026-09-16T11:00:00Z",
                    "source": work_source,
                },
                "events": [
                    {
                        "event_id": "demo-award",
                        "kind": "SPONSOR_AWARD",
                        "source": award_source,
                        "amount_minor": 250000,
                        "currency": "USD",
                    }
                ],
            }
        ],
    }
    registry_body = {
        "schema": "bounty-concierge/reward-settlement-trusted-sources/v1",
        "generated_at": GEN,
        "sources": [work_source, award_source],
    }
    registry = {
        **registry_body,
        "signature_hmac_sha256": hmac.new(
            KEY, certifier.canonical(registry_body), hashlib.sha256
        ).hexdigest(),
    }
    certificate = certifier.certify(document, registry, KEY)
    queue = compile_queue(
        document,
        registry,
        certificate,
        key=KEY,
        as_of="2026-09-16T13:00:00Z",
        max_evidence_age_seconds=86400,
    )
    row = queue["records"][0]
    assert row["queue_state"] == "PAYOUT_TICKET_FOLLOWUP_CANDIDATE"
    assert row["commercial_facts"]["sponsor_award"]["amount_minor"] == 250000
    assert row["authority"]["send_outbound"] is False
    assert row["authority"]["recognize_accounting_revenue"] is False
    print(row["queue_state"])
    print("send_outbound=false; recognize_accounting_revenue=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
