# SPDX-License-Identifier: MIT
"""Offline contract-risk preflight for already captured public bounty terms.

This is a negative-only guard. It never declares work eligible, paid, or safe.
A separate live same-payer historical-payment admission gate is mandatory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from typing import Any

SCHEMA = "bounty-contract-terms-preflight/v1"
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FIELDS = ("issue_title", "issue_body", "contributor_terms", "listing_text")
_MAX_FIELD = 200_000


class InvalidPacket(ValueError):
    """Malformed capture; never converted into an authorization."""


def _text(packet: dict[str, Any], key: str) -> str:
    value = packet.get(key, "")
    if not isinstance(value, str) or len(value) > _MAX_FIELD:
        raise InvalidPacket(f"{key} must be a string of at most {_MAX_FIELD} characters")
    return value


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def evaluate(packet: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(packet, dict):
        raise InvalidPacket("packet must be an object")
    repository = packet.get("repository_full_name")
    number = packet.get("issue_number")
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise InvalidPacket("repository_full_name must be owner/repo")
    if type(number) is not int or not 1 <= number <= 2_147_483_647:
        raise InvalidPacket("issue_number must be a positive integer")
    sources = {key: _text(packet, key) for key in _FIELDS}
    if not sources["issue_body"] and not sources["contributor_terms"] and not sources["listing_text"]:
        raise InvalidPacket("capture must include issue_body, contributor_terms or listing_text")

    flags: list[dict[str, str]] = []
    def flag(code: str, field: str, marker: str, next_step: str) -> None:
        # Fixed phrases only: untrusted issue text is never reproduced in output.
        flags.append({"code": code, "source_field": field, "source_sha256": _fingerprint(sources[field]),
                      "matched_indicator": marker, "required_resolution": next_step})

    for field, raw in sources.items():
        t = " ".join(raw.casefold().split())
        if not t:
            continue
        if ("right to terminate" in t and "internal" in t and "external" in t
                and ("merged" in t or "merge" in t)):
            flag("UNILATERAL_INTERNAL_RACE", field, "right to terminate + internal/external merge condition",
                 "Obtain sponsor's current written decision and payer history; no new unpaid build")
        if ("grantfox" in t and "apply" in t and "acknowledg" in t
                and ("before" in t or "first" in t)):
            flag("PREWORK_APPLICATION_ACK", field, "apply + GrantFox + acknowledged before work",
                 "Require proof that this exact contributor's pre-work application was acknowledged")
        if any(x in t for x in ("mock-payments mode", "mock payments only", "no real funds move",
                                 "not a real bounty", "test target, not a real")):
            flag("NONCASH_TEST_MODE", field, "explicit mock-payment or noncash disclosure",
                 "Exclude this task from paid-bounty dispatch; preserve pre-existing contributor claims")
        if (any(x in t for x in ("payment is automatic on merge", "payment is released automatically on merge",
                                 "automatically paid on merge")) and ("opire" in t or "stripe" in t)):
            flag("UNVERIFIED_AUTOPAY_AFFILIATION", field, "automatic payment on merge + payment provider name",
                 "Obtain independent provider confirmation and the creator's real payout terms")

    codes = {item["code"] for item in flags}
    if "NONCASH_TEST_MODE" in codes:
        decision = "EXCLUDE_NONCASH"
    elif codes & {"UNILATERAL_INTERNAL_RACE", "PREWORK_APPLICATION_ACK"}:
        decision = "HOLD_CONTRACT_RISK"
    elif codes:
        decision = "HOLD_PROVIDER_PROOF"
    else:
        decision = "NO_HAZARD_DETECTED_NOT_APPROVED"

    return {"schema": SCHEMA, "repository_full_name": repository, "issue_number": number,
            "decision": decision, "authorization_to_build": False,
            "financial_state": "UNKNOWN_UNVERIFIED",
            "flags": flags, "next_gate": "Live exact-payer paid-history, eligibility, amount and payment rail preflight",
            "capture_sha256": {key: _fingerprint(value) for key, value in sources.items() if value}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="captured JSON packet path, or - for stdin")
    args = parser.parse_args(argv)
    try:
        if args.input == "-":
            raw = sys.stdin.read()
        else:
            with open(args.input, encoding="utf-8") as fh:
                raw = fh.read()
        result = evaluate(json.loads(raw))
    except (InvalidPacket, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"schema": SCHEMA, "decision": "INVALID_CAPTURE_HOLD", "error": str(exc),
                          "authorization_to_build": False}), file=sys.stdout)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
