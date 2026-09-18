# SPDX-License-Identifier: MIT
"""Bind one verified paid-work economics receipt to one live claim-instruction target.

This module does not compile economics. It consumes the existing
paid_work_effort_value_gate receipt and narrows a verified GO to the exact work
identity and GitHub issue already established by a verified payoff claim proof.

The result authorizes only the local CLI to emit claim instructions. It does
not comment on GitHub, claim work externally, submit work, contact a sponsor,
move money, prove payment, or recognize revenue.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from concierge.paid_work_effort_value_gate import verify_receipt

PROOF_SCHEMA = "claim-economic-admission-proof/v1"
_PAYOFF_PROOF_SCHEMA = "payoff-claim-proof/v2"
_MAX_RECEIPT_BYTES = 1024 * 1024
_DEFAULT_MAX_AGE_SECONDS = 3600
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_EXPECTED_AUTHORITY = {
    "go_is_internal_admission_signal": True,
    "external_claim_authority": False,
    "external_submission_authority": False,
    "payment_cash_or_revenue_authority": False,
    "fx_conversion": False,
    "noncash_valuation": False,
}
_SUPPORTED_DECISIONS = frozenset(
    {"GO", "HOLD_VALUE_UNKNOWN", "HOLD_ACCOUNT_GATE", "SKIP_ECONOMICS"}
)


class ClaimEconomicAdmissionError(ValueError):
    """Safe, source-text-free failure at the live-claim economics boundary."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _sha256(value: Any, field: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_BINDING",
            f"{field} must be lowercase sha256",
        )
    return value


def _exact_utc(value: Any, field: str) -> tuple[str, datetime]:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_TIME",
            f"{field} must be exact UTC seconds",
        )
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_TIME",
            f"{field} is invalid",
        ) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_TIME",
            f"{field} is not canonical UTC",
        )
    return value, parsed


def _max_age(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 86400:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_BINDING",
            "max_age_seconds must be an integer in 1..86400",
        )
    return value


def _canonical_sha256(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_BINDING",
            "economic binding is not canonical JSON",
        ) from exc
    return hashlib.sha256(payload).hexdigest()


def _reject_float(raw: str) -> Any:
    raise ClaimEconomicAdmissionError(
        "INVALID_ECONOMIC_RECEIPT",
        "economic receipt contains a forbidden floating-point number",
    )


def _reject_constant(raw: str) -> Any:
    raise ClaimEconomicAdmissionError(
        "INVALID_ECONOMIC_RECEIPT",
        "economic receipt contains a forbidden non-finite constant",
    )


def _strict_json(payload: bytes) -> dict[str, Any]:
    if type(payload) is not bytes or not payload or len(payload) > _MAX_RECEIPT_BYTES:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic receipt has an invalid byte length",
        )
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic receipt must not contain a UTF-8 BOM",
        )
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic receipt is not strict UTF-8",
        ) from exc

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ClaimEconomicAdmissionError(
                    "INVALID_ECONOMIC_RECEIPT",
                    "economic receipt contains a duplicate JSON key",
                )
            result[key] = value
        return result

    try:
        parsed = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except ClaimEconomicAdmissionError:
        raise
    except json.JSONDecodeError as exc:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic receipt is not valid JSON",
        ) from exc
    if type(parsed) is not dict:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic receipt must be a JSON object",
        )
    return parsed


def _read_receipt(path: str | Path) -> bytes:
    source = Path(path)
    try:
        if source.is_symlink():
            raise ClaimEconomicAdmissionError(
                "INVALID_ECONOMIC_RECEIPT",
                "economic receipt path must not be a symlink",
            )
        stat = source.stat()
        if not source.is_file() or stat.st_size <= 0 or stat.st_size > _MAX_RECEIPT_BYTES:
            raise ClaimEconomicAdmissionError(
                "INVALID_ECONOMIC_RECEIPT",
                "economic receipt path is not one bounded regular file",
            )
        payload = source.read_bytes()
    except ClaimEconomicAdmissionError:
        raise
    except OSError as exc:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic receipt could not be read",
        ) from exc
    if len(payload) != stat.st_size:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic receipt changed while being read",
        )
    return payload


def _payoff_context(
    repo: str,
    issue: int,
    payoff_proof: dict[str, Any],
) -> tuple[str, str]:
    if type(payoff_proof) is not dict or payoff_proof.get("schema") != _PAYOFF_PROOF_SCHEMA:
        raise ClaimEconomicAdmissionError(
            "INVALID_PAYOFF_BINDING",
            "claim economics requires one verified payoff-claim proof",
        )
    if payoff_proof.get("repo") != repo or payoff_proof.get("issue") != issue:
        raise ClaimEconomicAdmissionError(
            "INVALID_PAYOFF_BINDING",
            "payoff proof target does not match the live claim target",
        )
    work_id = payoff_proof.get("work_id")
    if type(work_id) is not str or not work_id or work_id != work_id.strip():
        raise ClaimEconomicAdmissionError(
            "INVALID_PAYOFF_BINDING",
            "payoff proof work_id is unavailable",
        )
    canonical_issue_url = f"https://github.com/{repo}/issues/{issue}"
    if payoff_proof.get("canonical_issue_url") != canonical_issue_url:
        raise ClaimEconomicAdmissionError(
            "INVALID_PAYOFF_BINDING",
            "payoff proof canonical issue URL does not match the live claim target",
        )
    return work_id, canonical_issue_url


def verify_claim_economic_receipt(
    repo: str,
    issue: int,
    payoff_proof: dict[str, Any],
    receipt_path: str | Path,
    *,
    expected_receipt_bytes_sha256: str,
    expected_policy_sha256: str,
    decision_as_of: str,
    max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Verify one exact economics GO before live claim instructions are emitted."""

    work_id, canonical_issue_url = _payoff_context(repo, issue, payoff_proof)
    expected_bytes_sha = _sha256(
        expected_receipt_bytes_sha256,
        "expected_receipt_bytes_sha256",
    )
    expected_policy_sha = _sha256(
        expected_policy_sha256,
        "expected_policy_sha256",
    )
    decision_as_of_text, decision_dt = _exact_utc(
        decision_as_of,
        "decision_as_of",
    )
    max_age = _max_age(max_age_seconds)

    payload = _read_receipt(receipt_path)
    actual_bytes_sha = hashlib.sha256(payload).hexdigest()
    if actual_bytes_sha != expected_bytes_sha:
        raise ClaimEconomicAdmissionError(
            "ECONOMIC_RECEIPT_BYTES_MISMATCH",
            "economic receipt exact-byte sha256 mismatch",
        )

    receipt = _strict_json(payload)
    if verify_receipt(receipt) is not True:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic receipt failed self-integrity verification",
        )
    if receipt.get("work_id") != work_id:
        raise ClaimEconomicAdmissionError(
            "ECONOMIC_WORK_MISMATCH",
            "economic receipt work_id does not match the payoff proof",
        )
    if receipt.get("canonical_source_url") != canonical_issue_url:
        raise ClaimEconomicAdmissionError(
            "ECONOMIC_SOURCE_MISMATCH",
            "economic receipt source does not match the exact GitHub claim target",
        )

    receipt_policy_sha = _sha256(
        receipt.get("policy_sha256"),
        "economic receipt policy_sha256",
    )
    if receipt_policy_sha != expected_policy_sha:
        raise ClaimEconomicAdmissionError(
            "ECONOMIC_POLICY_MISMATCH",
            "economic receipt policy sha256 mismatch",
        )
    request_sha = _sha256(
        receipt.get("request_sha256"),
        "economic receipt request_sha256",
    )
    receipt_sha = _sha256(
        receipt.get("receipt_sha256"),
        "economic receipt receipt_sha256",
    )

    gate_as_of_text, gate_dt = _exact_utc(
        receipt.get("as_of"),
        "economic receipt as_of",
    )
    age_seconds = int((decision_dt - gate_dt).total_seconds())
    if age_seconds < 0:
        raise ClaimEconomicAdmissionError(
            "ECONOMIC_RECEIPT_FUTURE",
            "economic receipt is from the future",
        )
    if age_seconds > max_age:
        raise ClaimEconomicAdmissionError(
            "ECONOMIC_RECEIPT_STALE",
            "economic receipt is stale",
        )

    decision = receipt.get("decision")
    if decision not in _SUPPORTED_DECISIONS:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic receipt decision is unsupported",
        )

    authority = receipt.get("authority")
    if type(authority) is not dict or set(authority) != set(_EXPECTED_AUTHORITY):
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_AUTHORITY",
            "economic receipt authority schema is unsupported",
        )
    for key, expected in _EXPECTED_AUTHORITY.items():
        if authority.get(key) is not expected:
            raise ClaimEconomicAdmissionError(
                "INVALID_ECONOMIC_AUTHORITY",
                "economic receipt authority value is invalid",
            )

    binding = {
        "schema": PROOF_SCHEMA,
        "repo": repo,
        "issue": issue,
        "work_id": work_id,
        "canonical_issue_url": canonical_issue_url,
        "gate_receipt_bytes_sha256": actual_bytes_sha,
        "gate_receipt_sha256": receipt_sha,
        "gate_request_sha256": request_sha,
        "policy_sha256": receipt_policy_sha,
        "gate_as_of": gate_as_of_text,
        "decision_as_of": decision_as_of_text,
        "age_seconds": age_seconds,
        "max_age_seconds": max_age,
        "decision": decision,
    }
    binding_sha = _canonical_sha256(binding)

    if decision != "GO":
        raise ClaimEconomicAdmissionError(
            f"ECONOMICS_{decision}",
            "paid-work economics does not authorize live claim instructions",
        )

    return {
        **binding,
        "binding_sha256": binding_sha,
        "verified": True,
        "authority": "INTERNAL_CLAIM_INSTRUCTION_ADMISSION_ONLY_NO_EXTERNAL_ACTION",
    }
