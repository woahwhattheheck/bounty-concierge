# SPDX-License-Identifier: MIT
"""Synthetic demo for the certified settlement collections queue."""
import hashlib
import hmac
import json

from concierge.certified_settlement_collections_queue import build_queue
from concierge.reward_settlement_certifier import INPUT_SCHEMA, REGISTRY_SCHEMA, canonical

KEY = b"synthetic-demo-key-material-32bytes"

def source(source_id, authority, observed_at):
    return {"source_id":source_id,"source_ref":f"fixture://{source_id}","source_sha256":hashlib.sha256(source_id.encode()).hexdigest(),"observed_at":observed_at,"authority":authority}

work_source = source("merge-demo", "REPOSITORY", "2026-09-17T00:00:00Z")
award_source = source("award-demo", "SPONSOR", "2026-09-17T00:02:00Z")
document = {"schema":INPUT_SCHEMA,"generated_at":"2026-09-17T00:30:00Z","cases":[{"case_id":"demo-award","work":{"repo":"example/demo","pr":7,"merge_commit_sha":"7"*40,"merged_at":"2026-09-16T23:00:00Z","source":work_source},"events":[{"event_id":"award-demo","kind":"SPONSOR_AWARD","source":award_source,"amount_minor":25000,"currency":"USD"}]}]}
body = {"schema":REGISTRY_SCHEMA,"generated_at":"2026-09-17T00:30:00Z","sources":[work_source,award_source]}
registry = {**body,"signature_hmac_sha256":hmac.new(KEY,canonical(body),hashlib.sha256).hexdigest()}
print(json.dumps(build_queue(document,registry,KEY,as_of="2026-09-17T00:40:00Z"),indent=2,sort_keys=True))
