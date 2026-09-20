# SPDX-License-Identifier: MIT
"""Live deadline composition for GrantFox fixed-cash activation receipts.

The GrantFox dispatch path has separate semantic gates for provider/lifecycle,
canonical live cash, and sponsor deadlines. This final compositor prevents a
currently actionable GrantFox activation from surviving an elapsed campaign or
sponsor deadline simply because the canonical GitHub issue and dollar amount
remain open.

Deadline evidence is re-evaluated against the process UTC clock at composition
time; a historically green deadline receipt is therefore not permanent dispatch
authority. This module is advisory-only and grants no provider, repository,
submission, adjudication, contact, wallet, or payment authority.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .bounty_deadline_gate import (
    BountyDeadlineInputError,
    compile_bounty_deadline_gate,
    verify_receipt as verify_deadline_receipt,
)
from .grantfox_live_cash_activation import verify_live_cash_activation_receipt

SCHEMA = "grantfox-deadline-activation/v1"
RECEIPT_SCHEMA = "grantfox-deadline-activation-receipt/v1"
MAX_RECEIPT_AGE_SECONDS = 300
PASSTHROUGH = frozenset(
    {
        "APPLY_ELIGIBLE",
        "REPLAN_BEFORE_APPLY",
        "WAIT_ASSIGNMENT",
        "WAIT_DEPENDENCIES",
        "IMPLEMENT_ASSIGNED_SCOPE",
    }
)
UPSTREAM_HOLDS = frozenset({"HOLD_ECONOMICS", "HOLD_GRANTFOX_ACTIVATION"})
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
_REQUEST_KEYS = {"schema", "live_cash_activation_receipt", "deadline_receipt"}
_RECEIPT_KEYS = {
    "schema",
    "disposition",
    "advisory_next_action",
    "reason_codes",
    "identity",
    "activation",
    "deadline",
    "anchors",
    "evaluation",
    "evidence",
    "authority",
    "receipt_sha256",
}


class GrantFoxDeadlineActivationInputError(ValueError):
    """Raised when GrantFox activation/deadline evidence cannot compose safely."""


def _hash(value: Any) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _obj(value: Any, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise GrantFoxDeadlineActivationInputError(f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise GrantFoxDeadlineActivationInputError(
            f"{field} must be a non-empty trimmed string"
        )
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GrantFoxDeadlineActivationInputError(
            f"{field} must be a positive integer"
        )
    return value


def _clock(now: datetime | None = None) -> datetime:
    value = datetime.now(timezone.utc) if now is None else now
    if value.tzinfo is None or value.utcoffset() is None:
        raise GrantFoxDeadlineActivationInputError("now must be timezone-aware")
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_stamp(value: Any, field: str) -> datetime:
    raw = _text(value, field)
    if not raw.endswith("Z"):
        raise GrantFoxDeadlineActivationInputError(f"{field} must be UTC Z form")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise GrantFoxDeadlineActivationInputError(
            f"{field} must be an ISO-8601 UTC timestamp"
        ) from exc
    return parsed.astimezone(timezone.utc)


def _activation_identity(receipt: dict[str, Any]) -> tuple[str, str, int, str]:
    ident = _obj(receipt.get("identity"), "live_cash_activation_receipt.identity")
    return (
        _text(
            ident.get("owner"), "live_cash_activation_receipt.identity.owner"
        ).casefold(),
        _text(
            ident.get("repo"), "live_cash_activation_receipt.identity.repo"
        ).casefold(),
        _positive_int(
            ident.get("issue_number"),
            "live_cash_activation_receipt.identity.issue_number",
        ),
        _text(
            ident.get("actor_login"),
            "live_cash_activation_receipt.identity.actor_login",
        ).casefold(),
    )


def _deadline_identity(receipt: dict[str, Any]) -> tuple[str, str, int]:
    ident = _obj(receipt.get("identity"), "deadline_receipt.identity")
    return (
        _text(ident.get("owner"), "deadline_receipt.identity.owner").casefold(),
        _text(ident.get("repo"), "deadline_receipt.identity.repo").casefold(),
        _positive_int(
            ident.get("issue_number"), "deadline_receipt.identity.issue_number"
        ),
    )


def _deadline_request_at(
    receipt: dict[str, Any], evaluated_at: datetime
) -> dict[str, Any]:
    """Reconstruct the public deadline request at a fresh process-clock instant."""
    identity = _obj(receipt.get("identity"), "deadline_receipt.identity")
    observation = _obj(
        receipt.get("issue_observation"), "deadline_receipt.issue_observation"
    )
    deadline = _obj(receipt.get("deadline"), "deadline_receipt.deadline")
    evaluation = _obj(receipt.get("evaluation"), "deadline_receipt.evaluation")
    base = _obj(
        deadline.get("base_evidence"), "deadline_receipt.deadline.base_evidence"
    )
    extension = deadline.get("extension_evidence")
    if extension is not None and type(extension) is not dict:
        raise GrantFoxDeadlineActivationInputError(
            "deadline_receipt.deadline.extension_evidence must be object or null"
        )
    return {
        "schema": "bounty-deadline-gate/v1",
        "issue": {
            "url": _text(
                identity.get("issue_url"), "deadline_receipt.identity.issue_url"
            ),
            "state": _text(
                observation.get("state"),
                "deadline_receipt.issue_observation.state",
            ),
            "observed_at": _text(
                observation.get("observed_at"),
                "deadline_receipt.issue_observation.observed_at",
            ),
            "source_content_sha256": _text(
                observation.get("source_content_sha256"),
                "deadline_receipt.issue_observation.source_content_sha256",
            ),
        },
        "deadline_evidence": dict(base),
        "extension_evidence": dict(extension) if extension is not None else None,
        "evaluated_at": _stamp(evaluated_at),
        "max_observation_age_seconds": evaluation.get(
            "max_observation_age_seconds"
        ),
    }


def _current_deadline_receipt(
    receipt: dict[str, Any], evaluated_at: datetime
) -> dict[str, Any]:
    try:
        return compile_bounty_deadline_gate(
            _deadline_request_at(receipt, evaluated_at)
        )
    except (BountyDeadlineInputError, KeyError, TypeError, ValueError) as exc:
        raise GrantFoxDeadlineActivationInputError(
            f"deadline receipt cannot be re-evaluated safely: {exc}"
        ) from exc


def compile_deadline_activation(
    request: dict[str, Any],
    token: str | None = None,
    *,
    session: Any = None,
    max_pages: int = 10,
    saturation_threshold: int = 4,
    operator_login: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compose live fixed-cash GrantFox activation with current deadline truth."""
    request = _obj(request, "request")
    if set(request) != _REQUEST_KEYS:
        missing = _REQUEST_KEYS - set(request)
        extra = set(request) - _REQUEST_KEYS
        details = []
        if missing:
            details.append("missing=" + ",".join(sorted(missing)))
        if extra:
            details.append("extra=" + ",".join(sorted(extra)))
        raise GrantFoxDeadlineActivationInputError(
            "request keys must be exact"
            + (": " + " ".join(details) if details else "")
        )
    if request.get("schema") != SCHEMA:
        raise GrantFoxDeadlineActivationInputError(f"schema must equal {SCHEMA}")

    live = _obj(
        request.get("live_cash_activation_receipt"),
        "live_cash_activation_receipt",
    )
    deadline = _obj(request.get("deadline_receipt"), "deadline_receipt")

    live_kwargs = {
        "max_pages": max_pages,
        "saturation_threshold": saturation_threshold,
        "operator_login": operator_login,
    }
    if session is not None:
        live_kwargs["session"] = session
    if not verify_live_cash_activation_receipt(live, token, **live_kwargs):
        raise GrantFoxDeadlineActivationInputError(
            "live_cash_activation_receipt does not verify against current canonical state"
        )
    if not verify_deadline_receipt(deadline, semantic=True):
        raise GrantFoxDeadlineActivationInputError(
            "deadline_receipt does not verify semantically"
        )

    owner, repo, issue_number, actor = _activation_identity(live)
    d_owner, d_repo, d_issue = _deadline_identity(deadline)
    if (owner, repo, issue_number) != (d_owner, d_repo, d_issue):
        raise GrantFoxDeadlineActivationInputError(
            "activation and deadline receipts identify different issues"
        )

    evaluated = _clock(now)
    current_deadline = _current_deadline_receipt(deadline, evaluated)
    deadline_disposition = _text(
        current_deadline.get("disposition"), "current_deadline.disposition"
    )
    upstream_disposition = _text(
        live.get("disposition"), "live_cash_activation_receipt.disposition"
    )
    if (
        upstream_disposition not in PASSTHROUGH
        and upstream_disposition not in UPSTREAM_HOLDS
    ):
        raise GrantFoxDeadlineActivationInputError(
            "live_cash_activation_receipt emitted an unsupported disposition"
        )
    if deadline_disposition not in {"DEADLINE_CURRENT", "HOLD"}:
        raise GrantFoxDeadlineActivationInputError(
            "deadline gate emitted an unsupported disposition"
        )

    reasons: list[str] = []
    if upstream_disposition in UPSTREAM_HOLDS:
        disposition = "HOLD_UPSTREAM"
        reasons.append("GRANTFOX_LIVE_CASH_ACTIVATION_NOT_ACTIONABLE")
        next_action = "RESOLVE_UPSTREAM_GRANTFOX_OR_ECONOMICS_HOLD"
    elif deadline_disposition != "DEADLINE_CURRENT":
        disposition = "HOLD_DEADLINE"
        reasons.append("SPONSOR_OR_CAMPAIGN_DEADLINE_NOT_CURRENT")
        next_action = _text(
            current_deadline.get("advisory_next_action"),
            "current_deadline.advisory_next_action",
        )
    else:
        disposition = upstream_disposition
        next_action = _text(
            live.get("advisory_next_action"),
            "live_cash_activation_receipt.advisory_next_action",
        )

    live_digest = _text(
        live.get("receipt_sha256"),
        "live_cash_activation_receipt.receipt_sha256",
    )
    deadline_digest = _text(
        deadline.get("receipt_sha256"), "deadline_receipt.receipt_sha256"
    )
    current_deadline_digest = _text(
        current_deadline.get("receipt_sha256"),
        "current_deadline.receipt_sha256",
    )
    for value, field in (
        (live_digest, "live cash activation receipt"),
        (deadline_digest, "deadline receipt"),
        (current_deadline_digest, "current deadline receipt"),
    ):
        if _SHA256.fullmatch(value) is None:
            raise GrantFoxDeadlineActivationInputError(
                f"{field} digest must be lowercase sha256"
            )

    current_deadline_reasons = current_deadline.get("reason_codes")
    if type(current_deadline_reasons) is not list or any(
        type(value) is not str for value in current_deadline_reasons
    ):
        raise GrantFoxDeadlineActivationInputError(
            "current_deadline.reason_codes must be a list of strings"
        )
    deadline_view = _obj(
        current_deadline.get("deadline"), "current_deadline.deadline"
    )
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
        "activation": {"disposition": upstream_disposition},
        "deadline": {
            "source_disposition": deadline.get("disposition"),
            "current_disposition": deadline_disposition,
            "effective_kind": deadline_view.get("effective_kind"),
            "effective_value": deadline_view.get("effective_value"),
            "relation_at_composition": deadline_view.get(
                "relation_at_evaluation"
            ),
            "extension_status": deadline_view.get("extension_status"),
            "current_reason_codes": list(current_deadline_reasons),
        },
        "anchors": {
            "live_cash_activation_receipt_sha256": live_digest,
            "deadline_receipt_sha256": deadline_digest,
            "current_deadline_receipt_sha256": current_deadline_digest,
        },
        "evaluation": {
            "composed_at": _stamp(evaluated),
            "max_receipt_age_seconds": MAX_RECEIPT_AGE_SECONDS,
        },
        "evidence": {
            "live_cash_activation_receipt": live,
            "deadline_receipt": deadline,
            "current_deadline_receipt": current_deadline,
        },
        "authority": dict(AUTHORITY),
    }
    return {**body, "receipt_sha256": _hash(body)}


def verify_deadline_activation_receipt(
    receipt: dict[str, Any],
    token: str | None = None,
    *,
    session: Any = None,
    max_pages: int = 10,
    saturation_threshold: int = 4,
    operator_login: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Verify integrity, nested semantics, freshness, and current deadline state."""
    if type(receipt) is not dict or set(receipt) != _RECEIPT_KEYS:
        return False
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("authority") != AUTHORITY
    ):
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
        "live_cash_activation_receipt",
        "deadline_receipt",
        "current_deadline_receipt",
    }:
        return False
    live = evidence.get("live_cash_activation_receipt")
    deadline = evidence.get("deadline_receipt")
    current_deadline = evidence.get("current_deadline_receipt")
    if (
        type(live) is not dict
        or type(deadline) is not dict
        or type(current_deadline) is not dict
    ):
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
        composed_at = _parse_stamp(
            evaluation.get("composed_at"), "evaluation.composed_at"
        )
        current_time = _clock(now)
    except GrantFoxDeadlineActivationInputError:
        return False
    age = (current_time - composed_at).total_seconds()
    if age < 0 or age > MAX_RECEIPT_AGE_SECONDS:
        return False

    try:
        expected = compile_deadline_activation(
            {
                "schema": SCHEMA,
                "live_cash_activation_receipt": live,
                "deadline_receipt": deadline,
            },
            token,
            session=session,
            max_pages=max_pages,
            saturation_threshold=saturation_threshold,
            operator_login=operator_login,
            now=composed_at,
        )
    except (
        GrantFoxDeadlineActivationInputError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return False
    if expected != receipt:
        return False

    try:
        live_current_deadline = _current_deadline_receipt(
            deadline, current_time
        )
    except GrantFoxDeadlineActivationInputError:
        return False
    if receipt.get("disposition") in PASSTHROUGH:
        if live_current_deadline.get("disposition") != "DEADLINE_CURRENT":
            return False
    return True


def format_summary(receipt: dict[str, Any]) -> str:
    identity = receipt["identity"]
    reasons = ",".join(receipt["reason_codes"]) or "none"
    return (
        f"{identity['owner']}/{identity['repo']}#{identity['issue_number']} "
        f"activation={receipt['activation']['disposition']} "
        f"deadline={receipt['deadline']['current_disposition']} "
        f"disposition={receipt['disposition']} reasons={reasons} "
        f"receipt_sha256={receipt['receipt_sha256']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.grantfox_deadline_activation",
        description=(
            "Compose live GrantFox fixed-cash activation with current "
            "sponsor/campaign deadline truth."
        ),
    )
    parser.add_argument("request", help="request JSON path, or - for stdin")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--saturation-threshold", type=int, default=4)
    parser.add_argument("--operator-login")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.request == "-":
            import sys

            payload = json.load(sys.stdin)
        else:
            payload = json.loads(
                Path(args.request).read_text(encoding="utf-8")
            )
        receipt = compile_deadline_activation(
            payload,
            max_pages=args.max_pages,
            saturation_threshold=args.saturation_threshold,
            operator_login=args.operator_login,
        )
    except (
        OSError,
        json.JSONDecodeError,
        GrantFoxDeadlineActivationInputError,
    ) as exc:
        parser.error(str(exc))
    print(
        json.dumps(receipt, indent=2, sort_keys=True)
        if args.json
        else format_summary(receipt)
    )
    return 0 if receipt["disposition"] in PASSTHROUGH else 2


if __name__ == "__main__":
    raise SystemExit(main())
