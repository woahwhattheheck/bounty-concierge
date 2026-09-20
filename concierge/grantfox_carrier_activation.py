# SPDX-License-Identifier: MIT
"""Final carrier-census fence for GrantFox live fixed-cash activation.

GrantFox's provider queue can expose linked pull requests, while the independent
carrier census captures additional same-issue implementation carriers that may
not be linked by the provider. This compositor prevents an otherwise actionable
live-cash/deadline receipt from being dispatched as fresh work when current
carrier evidence says to reuse or review existing work.

Carrier evidence is re-evaluated against the process UTC clock at composition
and verification time. The module is advisory-only and grants no provider,
repository, submission, contact, wallet, or payment authority.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .grantfox_carrier_census import (
    GrantFoxCarrierCensusInputError,
    compile_grantfox_carrier_census,
    verify_carrier_census_receipt,
)
from .grantfox_deadline_activation import (
    PASSTHROUGH,
    verify_deadline_activation_receipt,
)

SCHEMA = "grantfox-carrier-activation/v1"
RECEIPT_SCHEMA = "grantfox-carrier-activation-receipt/v1"
MAX_RECEIPT_AGE_SECONDS = 300
UPSTREAM_HOLDS = frozenset({"HOLD_UPSTREAM", "HOLD_DEADLINE"})
AUTHORITY = {
    "advisory_only": True,
    "provider_application_authority": False,
    "implementation_write_authority": False,
    "repository_write_authority": False,
    "submission_authority": False,
    "adjudication_authority": False,
    "outbound_contact_authority": False,
    "payment_or_wallet_authority": False,
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_KEYS = {"schema", "deadline_activation_receipt", "carrier_census_receipt"}
_RECEIPT_KEYS = {
    "schema",
    "disposition",
    "advisory_next_action",
    "reason_codes",
    "identity",
    "activation",
    "carrier",
    "anchors",
    "evaluation",
    "evidence",
    "authority",
    "receipt_sha256",
}


class GrantFoxCarrierActivationInputError(ValueError):
    """Raised when deadline-activation and carrier evidence cannot compose safely."""


def _hash(value: Any) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _obj(value: Any, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise GrantFoxCarrierActivationInputError(f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise GrantFoxCarrierActivationInputError(
            f"{field} must be a non-empty trimmed string"
        )
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GrantFoxCarrierActivationInputError(f"{field} must be a positive integer")
    return value


def _clock(now: datetime | None = None) -> datetime:
    value = datetime.now(timezone.utc) if now is None else now
    if value.tzinfo is None or value.utcoffset() is None:
        raise GrantFoxCarrierActivationInputError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_stamp(value: Any, field: str) -> datetime:
    raw = _text(value, field)
    if not raw.endswith("Z"):
        raise GrantFoxCarrierActivationInputError(f"{field} must be UTC Z form")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise GrantFoxCarrierActivationInputError(f"{field} must be valid RFC3339") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise GrantFoxCarrierActivationInputError(f"{field} must be UTC")
    return parsed


def _activation_identity(receipt: dict[str, Any]) -> tuple[str, str, int, str | None]:
    identity = _obj(receipt.get("identity"), "deadline_activation_receipt.identity")
    actor = identity.get("actor_login")
    if actor is not None:
        actor = _text(actor, "deadline_activation_receipt.identity.actor_login").casefold()
    return (
        _text(identity.get("owner"), "deadline_activation_receipt.identity.owner").casefold(),
        _text(identity.get("repo"), "deadline_activation_receipt.identity.repo").casefold(),
        _positive_int(
            identity.get("issue_number"),
            "deadline_activation_receipt.identity.issue_number",
        ),
        actor,
    )


def _carrier_identity(receipt: dict[str, Any]) -> tuple[str, str, int]:
    identity = _obj(receipt.get("identity"), "carrier_census_receipt.identity")
    return (
        _text(identity.get("owner"), "carrier_census_receipt.identity.owner").casefold(),
        _text(identity.get("repo"), "carrier_census_receipt.identity.repo").casefold(),
        _positive_int(
            identity.get("issue_number"),
            "carrier_census_receipt.identity.issue_number",
        ),
    )


def _current_carrier_receipt(
    receipt: dict[str, Any],
    evaluated_at: datetime,
) -> dict[str, Any]:
    """Recompile embedded carrier observations at a fresh process-clock instant."""
    identity = _obj(receipt.get("identity"), "carrier_census_receipt.identity")
    census = _obj(receipt.get("census"), "carrier_census_receipt.census")
    carriers = census.get("carriers")
    if type(carriers) is not list:
        raise GrantFoxCarrierActivationInputError(
            "carrier_census_receipt.census.carriers must be a list"
        )
    request = {
        "schema": "grantfox-carrier-census/v1",
        "canonical_issue_url": _text(
            identity.get("canonical_issue_url"),
            "carrier_census_receipt.identity.canonical_issue_url",
        ),
        "carriers": [
            {
                "pr_url": carrier.get("pr_url"),
                "state": carrier.get("state"),
                "issue_relation": carrier.get("issue_relation"),
                "process_disposition": carrier.get("process_disposition"),
                "head_sha": carrier.get("head_sha"),
            }
            for carrier in carriers
            if type(carrier) is dict
        ],
        "observed_at": _text(
            census.get("observed_at"), "carrier_census_receipt.census.observed_at"
        ),
        "evaluated_at": _stamp(evaluated_at),
        "max_snapshot_age_seconds": census.get("max_snapshot_age_seconds"),
    }
    if len(request["carriers"]) != len(carriers):
        raise GrantFoxCarrierActivationInputError(
            "carrier_census_receipt.census.carriers contains a non-object"
        )
    try:
        return compile_grantfox_carrier_census(request)
    except (GrantFoxCarrierCensusInputError, KeyError, TypeError, ValueError) as exc:
        raise GrantFoxCarrierActivationInputError(
            f"carrier census cannot be re-evaluated safely: {exc}"
        ) from exc


def compile_carrier_activation(
    request: dict[str, Any],
    token: str | None = None,
    *,
    session: Any = None,
    max_pages: int = 10,
    saturation_threshold: int = 4,
    operator_login: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compose current GrantFox deadline activation with current carrier truth."""
    request = _obj(request, "request")
    missing = _REQUEST_KEYS - set(request)
    extra = set(request) - _REQUEST_KEYS
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing={sorted(missing)}")
        if extra:
            details.append(f"unsupported={sorted(extra)}")
        raise GrantFoxCarrierActivationInputError(
            "request fields must match schema exactly: " + " ".join(details)
        )
    if request.get("schema") != SCHEMA:
        raise GrantFoxCarrierActivationInputError(f"schema must equal {SCHEMA}")

    activation = _obj(
        request.get("deadline_activation_receipt"), "deadline_activation_receipt"
    )
    carrier = _obj(request.get("carrier_census_receipt"), "carrier_census_receipt")
    evaluated = _clock(now)

    if not verify_deadline_activation_receipt(
        activation,
        token,
        session=session,
        max_pages=max_pages,
        saturation_threshold=saturation_threshold,
        operator_login=operator_login,
        now=evaluated,
    ):
        raise GrantFoxCarrierActivationInputError(
            "deadline_activation_receipt does not verify against current state"
        )
    if not verify_carrier_census_receipt(carrier):
        raise GrantFoxCarrierActivationInputError(
            "carrier_census_receipt does not verify semantically"
        )

    owner, repo, issue_number, actor = _activation_identity(activation)
    c_owner, c_repo, c_issue = _carrier_identity(carrier)
    if (owner, repo, issue_number) != (c_owner, c_repo, c_issue):
        raise GrantFoxCarrierActivationInputError(
            "activation and carrier receipts identify different issues"
        )

    upstream_disposition = _text(
        activation.get("disposition"), "deadline_activation_receipt.disposition"
    )
    if upstream_disposition not in PASSTHROUGH and upstream_disposition not in UPSTREAM_HOLDS:
        raise GrantFoxCarrierActivationInputError(
            "deadline_activation_receipt emitted an unsupported disposition"
        )

    current_carrier = _current_carrier_receipt(carrier, evaluated)
    carrier_disposition = _text(
        current_carrier.get("disposition"), "current_carrier_census_receipt.disposition"
    )
    carrier_action = _text(
        current_carrier.get("advisory_next_action"),
        "current_carrier_census_receipt.advisory_next_action",
    )
    carrier_reasons = current_carrier.get("reason_codes")
    if type(carrier_reasons) is not list or any(type(value) is not str for value in carrier_reasons):
        raise GrantFoxCarrierActivationInputError(
            "current_carrier_census_receipt.reason_codes must be a list of strings"
        )

    reasons: list[str] = []
    if upstream_disposition not in PASSTHROUGH:
        disposition = "HOLD_UPSTREAM"
        reasons.append("GRANTFOX_DEADLINE_ACTIVATION_NOT_ACTIONABLE")
        next_action = _text(
            activation.get("advisory_next_action"),
            "deadline_activation_receipt.advisory_next_action",
        )
    elif carrier_disposition == "CLEAR_FOR_QUEUE_EVALUATION":
        disposition = upstream_disposition
        next_action = _text(
            activation.get("advisory_next_action"),
            "deadline_activation_receipt.advisory_next_action",
        )
    elif carrier_disposition == "REUSE_EXISTING_CARRIER":
        disposition = "HOLD_EXISTING_CARRIER"
        reasons.append("ACTIVE_OR_MERGED_CARRIER_PRESENT")
        next_action = carrier_action
    elif carrier_disposition == "REAPPLY_WITH_REUSABLE_CARRIER":
        disposition = "HOLD_REUSABLE_CARRIER"
        reasons.append("PROCESS_CLOSED_REUSABLE_CARRIER_PRESENT")
        next_action = carrier_action
    elif carrier_disposition == "REVIEW_CLOSED_CARRIER":
        disposition = "HOLD_CLOSED_CARRIER_REVIEW"
        reasons.append("CLOSED_CARRIER_REVIEW_REQUIRED")
        next_action = carrier_action
    elif carrier_disposition == "HOLD":
        disposition = "HOLD_CARRIER_CENSUS"
        reasons.append("CARRIER_CENSUS_STALE")
        next_action = carrier_action
    else:
        raise GrantFoxCarrierActivationInputError(
            "carrier_census_receipt emitted an unsupported disposition"
        )

    activation_digest = _text(
        activation.get("receipt_sha256"), "deadline_activation_receipt.receipt_sha256"
    )
    carrier_digest = _text(
        carrier.get("carrier_receipt_sha256"),
        "carrier_census_receipt.carrier_receipt_sha256",
    )
    current_carrier_digest = _text(
        current_carrier.get("carrier_receipt_sha256"),
        "current_carrier_census_receipt.carrier_receipt_sha256",
    )
    for digest, field in (
        (activation_digest, "deadline activation receipt"),
        (carrier_digest, "carrier census receipt"),
        (current_carrier_digest, "current carrier census receipt"),
    ):
        if _SHA256.fullmatch(digest) is None:
            raise GrantFoxCarrierActivationInputError(f"{field} digest must be lowercase sha256")

    census_view = _obj(current_carrier.get("census"), "current_carrier_census_receipt.census")
    body = {
        "schema": RECEIPT_SCHEMA,
        "disposition": disposition,
        "advisory_next_action": next_action,
        "reason_codes": reasons,
        "identity": {
            "owner": owner,
            "repo": repo,
            "issue_number": issue_number,
            "actor_login": actor,
        },
        "activation": {
            "disposition": upstream_disposition,
        },
        "carrier": {
            "source_disposition": carrier.get("disposition"),
            "current_disposition": carrier_disposition,
            "current_reason_codes": list(carrier_reasons),
            "observed_pr_count": census_view.get("observed_pr_count"),
            "relevant_carrier_count": census_view.get("relevant_carrier_count"),
            "active_or_merged_count": census_view.get("active_or_merged_count"),
            "process_closed_count": census_view.get("process_closed_count"),
            "other_closed_count": census_view.get("other_closed_count"),
        },
        "anchors": {
            "deadline_activation_receipt_sha256": activation_digest,
            "carrier_census_receipt_sha256": carrier_digest,
            "current_carrier_census_receipt_sha256": current_carrier_digest,
        },
        "evaluation": {
            "composed_at": _stamp(evaluated),
            "max_receipt_age_seconds": MAX_RECEIPT_AGE_SECONDS,
        },
        "evidence": {
            "deadline_activation_receipt": activation,
            "carrier_census_receipt": carrier,
            "current_carrier_census_receipt": current_carrier,
        },
        "authority": dict(AUTHORITY),
    }
    return {**body, "receipt_sha256": _hash(body)}


def verify_carrier_activation_receipt(
    receipt: dict[str, Any],
    token: str | None = None,
    *,
    session: Any = None,
    max_pages: int = 10,
    saturation_threshold: int = 4,
    operator_login: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Verify integrity, nested semantics, freshness, and current carrier state."""
    if type(receipt) is not dict or set(receipt) != _RECEIPT_KEYS:
        return False
    if receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("authority") != AUTHORITY:
        return False
    digest = receipt.get("receipt_sha256")
    if type(digest) is not str or _SHA256.fullmatch(digest) is None:
        return False
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    if _hash(body) != digest:
        return False

    evidence = receipt.get("evidence")
    if type(evidence) is not dict or set(evidence) != {
        "deadline_activation_receipt",
        "carrier_census_receipt",
        "current_carrier_census_receipt",
    }:
        return False
    activation = evidence.get("deadline_activation_receipt")
    carrier = evidence.get("carrier_census_receipt")
    current_carrier = evidence.get("current_carrier_census_receipt")
    if type(activation) is not dict or type(carrier) is not dict or type(current_carrier) is not dict:
        return False

    evaluation = receipt.get("evaluation")
    if type(evaluation) is not dict or set(evaluation) != {
        "composed_at",
        "max_receipt_age_seconds",
    }:
        return False
    if evaluation.get("max_receipt_age_seconds") != MAX_RECEIPT_AGE_SECONDS:
        return False
    try:
        composed_at = _parse_stamp(evaluation.get("composed_at"), "evaluation.composed_at")
        current_time = _clock(now)
    except GrantFoxCarrierActivationInputError:
        return False
    age = (current_time - composed_at).total_seconds()
    if age < 0 or age > MAX_RECEIPT_AGE_SECONDS:
        return False

    try:
        expected = compile_carrier_activation(
            {
                "schema": SCHEMA,
                "deadline_activation_receipt": activation,
                "carrier_census_receipt": carrier,
            },
            token,
            session=session,
            max_pages=max_pages,
            saturation_threshold=saturation_threshold,
            operator_login=operator_login,
            now=composed_at,
        )
    except (GrantFoxCarrierActivationInputError, KeyError, TypeError, ValueError):
        return False
    if expected != receipt:
        return False

    if not verify_deadline_activation_receipt(
        activation,
        token,
        session=session,
        max_pages=max_pages,
        saturation_threshold=saturation_threshold,
        operator_login=operator_login,
        now=current_time,
    ):
        return False

    try:
        live_carrier = _current_carrier_receipt(carrier, current_time)
    except GrantFoxCarrierActivationInputError:
        return False
    if receipt.get("disposition") in PASSTHROUGH:
        if live_carrier.get("disposition") != "CLEAR_FOR_QUEUE_EVALUATION":
            return False
    return True


def format_summary(receipt: dict[str, Any]) -> str:
    identity = receipt["identity"]
    reasons = ",".join(receipt["reason_codes"]) or "none"
    return (
        f"{identity['owner']}/{identity['repo']}#{identity['issue_number']} "
        f"activation={receipt['activation']['disposition']} "
        f"carrier={receipt['carrier']['current_disposition']} "
        f"disposition={receipt['disposition']} "
        f"next={receipt['advisory_next_action']} reasons={reasons} "
        f"receipt_sha256={receipt['receipt_sha256']}"
    )


def _load_request(path: str) -> dict[str, Any]:
    if path == "-":
        import sys
        return json.load(sys.stdin)
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.grantfox_carrier_activation",
        description="Compose GrantFox deadline activation with current carrier-census truth.",
    )
    parser.add_argument("request", help="carrier-activation request JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true", help="emit full receipt JSON")
    args = parser.parse_args(argv)
    try:
        receipt = compile_carrier_activation(_load_request(args.request))
    except (OSError, json.JSONDecodeError, GrantFoxCarrierActivationInputError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(receipt, sort_keys=True, indent=2))
    else:
        print(format_summary(receipt))
    return 0 if receipt["disposition"] in PASSTHROUGH else 2


if __name__ == "__main__":
    raise SystemExit(main())
