#!/usr/bin/env python3
"""Synthetic, provider-free review-packet demo for a certified settlement queue."""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from concierge.certified_settlement_collection_review_packet import (
    FALSE_AUTHORITY,
    QUEUE_SCHEMA,
    ROUTES_SCHEMA,
    _sha,
    build_review_packet,
)


def main() -> int:
    body = {
        "schema": QUEUE_SCHEMA,
        "input_generated_at": "2026-09-17T00:30:00Z",
        "trusted_registry_generated_at": "2026-09-17T00:30:00Z",
        "as_of": "2026-09-17T01:00:00Z",
        "freshness_seconds": 3600,
        "certificate_receipt_sha256": "a" * 64,
        "trusted_registry_body_sha256": "b" * 64,
        "records": [
            {
                "case_id": "synthetic-award-followup",
                "work": {"repo": "example/repo", "pr": 7},
                "queue_state": "AWARD_FOLLOWUP_CANDIDATE",
                "reason_codes": ["CERTIFIED_AWARD_WITHOUT_PAYOUT_TICKET"],
                "money": {"basis": "SPONSOR_AWARD", "amount_minor": 9000, "currency": "USD", "unit": "minor"},
                "event_age_seconds": 300,
            },
            {
                "case_id": "synthetic-settled",
                "work": {"repo": "example/repo", "pr": 8},
                "queue_state": "SETTLED",
                "reason_codes": ["CERTIFIED_CONFIRMED_TRANSFER"],
                "money": {"basis": "SPONSOR_AWARD", "amount_minor": 12000, "currency": "USD", "unit": "minor"},
                "event_age_seconds": 120,
            },
        ],
        "aggregates": {"record_count": 2, "queue_state_counts": {"AWARD_FOLLOWUP_CANDIDATE": 1, "SETTLED": 1}},
        "authority": dict(FALSE_AUTHORITY),
    }
    queue = {**body, "receipt_sha256": _sha(body)}
    routes = {
        "schema": ROUTES_SCHEMA,
        "as_of": queue["as_of"],
        "routes": [
            {
                "case_id": "synthetic-award-followup",
                "route_id": "sponsor-ticket-route",
                "kind": "TICKET",
                "route_ref": "fixture://contact/ticket/7",
                "disposition": "AVAILABLE",
                "source_ref": "fixture://contact-source/7",
                "source_sha256": "c" * 64,
                "observed_at": "2026-09-17T00:59:00Z",
            }
        ],
    }
    packet = build_review_packet(queue, routes)
    assert packet["records"][0]["outbound_authorized"] is False
    assert packet["authority"] == FALSE_AUTHORITY
    print(json.dumps({
        "actions": {row["case_id"]: row["next_action_code"] for row in packet["records"]},
        "authority": packet["authority"],
        "packet_receipt_sha256": packet["receipt_sha256"],
        "queue_receipt_sha256": packet["queue_receipt_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
