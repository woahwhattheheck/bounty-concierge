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
import os
from pathlib import Path
import re
import stat as stat_module
from typing import Any

from concierge.paid_work_effort_value_gate import (
    PaidWorkGateInputError,
    compile_paid_work_effort_value_gate,
    verify_receipt,
)

PROOF_SCHEMA = "claim-economic-admission-proof/v2"
_PAYOFF_PROOF_SCHEMA = "payoff-claim-proof/v2"
_MAX_RECEIPT_BYTES = 1024 * 1024
_DEFAULT_MAX_AGE_SECONDS = 3600
_MAX_SAFE_JSON_INT = (1 << 53) - 1
_MAX_SAFE_JSON_INT_DIGITS = len(str(_MAX_SAFE_JSON_INT))
_EXPECTED_POLICY_SHA256 = "1d0833b700e886b77a83c156ebf06b57b315f9fff489acd3e64878ac028a57a6"
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
        "economic artifact contains a forbidden non-finite constant",
    )


def _parse_int(raw: str) -> int:
    digits = raw[1:] if raw.startswith("-") else raw
    if not digits or len(digits) > _MAX_SAFE_JSON_INT_DIGITS:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic artifact contains an unsafe integer",
        )
    try:
        value = int(raw)
    except ValueError as exc:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic artifact contains an unsafe integer",
        ) from exc
    if abs(value) > _MAX_SAFE_JSON_INT:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic artifact contains an unsafe integer",
        )
    return value


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
            parse_int=_parse_int,
        )
    except ClaimEconomicAdmissionError:
        raise
    except (json.JSONDecodeError, ValueError, OverflowError, RecursionError) as exc:
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


def _read_bounded_regular(path: str | Path) -> bytes:
    source = Path(path)
    try:
        initial = source.lstat()
    except OSError as exc:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic artifact could not be statted",
        ) from exc
    if stat_module.S_ISLNK(initial.st_mode) or not stat_module.S_ISREG(initial.st_mode):
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic artifact path must be one real regular file",
        )
    if initial.st_size <= 0 or initial.st_size > _MAX_RECEIPT_BYTES:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic artifact has an invalid byte length",
        )

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(source, flags)
    except OSError as exc:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic artifact could not be opened without following links",
        ) from exc
    try:
        opened = os.fstat(fd)
        if (
            not stat_module.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino)
            or opened.st_size != initial.st_size
        ):
            raise ClaimEconomicAdmissionError(
                "INVALID_ECONOMIC_RECEIPT",
                "economic artifact generation changed before read",
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_RECEIPT_BYTES:
                raise ClaimEconomicAdmissionError(
                    "INVALID_ECONOMIC_RECEIPT",
                    "economic artifact grew beyond byte limit",
                )
            chunks.append(chunk)
        after = os.fstat(fd)
        if (
            (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            or after.st_size != opened.st_size
            or getattr(after, "st_mtime_ns", int(after.st_mtime * 1_000_000_000))
            != getattr(opened, "st_mtime_ns", int(opened.st_mtime * 1_000_000_000))
        ):
            raise ClaimEconomicAdmissionError(
                "INVALID_ECONOMIC_RECEIPT",
                "economic artifact changed while being read",
            )
        payload = b"".join(chunks)
        if len(payload) != opened.st_size:
            raise ClaimEconomicAdmissionError(
                "INVALID_ECONOMIC_RECEIPT",
                "economic artifact byte count changed while being read",
            )
        return payload
    except ClaimEconomicAdmissionError:
        raise
    except OSError as exc:
        raise ClaimEconomicAdmissionError(
            "INVALID_ECONOMIC_RECEIPT",
            "economic artifact could not be read",
        ) from exc
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

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


def _build_verify_claim_economic_receipt(
    *,
    payoff_context,
    exact_utc,
    max_age,
    read_bounded_regular,
    sha256_digest,
    strict_json,
    compile_gate,
    gate_input_error,
    sha256_field,
    canonical_sha256,
    verify_gate_receipt,
    expected_policy_sha256,
    expected_authority_items,
    supported_decisions,
    error_type,
    proof_schema,
):
    """Freeze the live economic-admission dependency generation at first import."""
    expected_policy_sha256 = str(expected_policy_sha256)
    expected_authority_items = tuple(expected_authority_items)
    expected_authority_keys = frozenset(key for key, _ in expected_authority_items)
    supported_decisions = frozenset(supported_decisions)
    proof_schema = str(proof_schema)

    def verify_claim_economic_receipt(
        repo: str,
        issue: int,
        payoff_proof: dict[str, Any],
        request_path: str | Path,
        receipt_path: str | Path,
        *,
        decision_as_of: str,
        max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS,
    ) -> dict[str, Any]:
        """Verify one exact economics GO before live claim instructions are emitted."""
    
        work_id, canonical_issue_url = payoff_context(repo, issue, payoff_proof)
        decision_as_of_text, decision_dt = exact_utc(
            decision_as_of,
            "decision_as_of",
        )
        max_age = max_age(max_age_seconds)
    
        request_payload = read_bounded_regular(request_path)
        request_bytes_sha = sha256_digest(request_payload).hexdigest()
        request = strict_json(request_payload)
        try:
            replayed_receipt = compile_gate(request)
        except gate_input_error as exc:
            raise error_type(
                "INVALID_ECONOMIC_REQUEST",
                "economic request failed paid-work semantic replay",
            ) from exc
        replayed_policy_sha = sha256_field(
            replayed_receipt.get("policysha256_field"),
            "replayed economic policysha256_field",
        )
        if replayed_policy_sha != expected_policy_sha256:
            raise error_type(
                "ECONOMIC_POLICY_MISMATCH",
                "economic request does not use the source-owned admission policy",
            )
    
        payload = read_bounded_regular(receipt_path)
        actual_bytes_sha = sha256_digest(payload).hexdigest()
        receipt = strict_json(payload)
        if _canonicalsha256_field(receipt) != _canonicalsha256_field(replayed_receipt):
            raise error_type(
                "ECONOMIC_RECEIPT_REPLAY_MISMATCH",
                "economic receipt does not equal deterministic replay of its retained request",
            )
        if verify_gate_receipt(receipt) is not True:
            raise error_type(
                "INVALID_ECONOMIC_RECEIPT",
                "economic receipt failed self-integrity verification",
            )
        if receipt.get("work_id") != work_id:
            raise error_type(
                "ECONOMIC_WORK_MISMATCH",
                "economic receipt work_id does not match the payoff proof",
            )
        if receipt.get("canonical_source_url") != canonical_issue_url:
            raise error_type(
                "ECONOMIC_SOURCE_MISMATCH",
                "economic receipt source does not match the exact GitHub claim target",
            )
    
        receipt_policy_sha = sha256_field(
            receipt.get("policysha256_field"),
            "economic receipt policysha256_field",
        )
        if receipt_policy_sha != expected_policy_sha256:
            raise error_type(
                "ECONOMIC_POLICY_MISMATCH",
                "economic receipt policy sha256 mismatch",
            )
        request_sha = sha256_field(
            receipt.get("requestsha256_field"),
            "economic receipt requestsha256_field",
        )
        receipt_sha = sha256_field(
            receipt.get("receiptsha256_field"),
            "economic receipt receiptsha256_field",
        )
    
        gate_as_of_text, gate_dt = exact_utc(
            receipt.get("as_of"),
            "economic receipt as_of",
        )
        age_seconds = int((decision_dt - gate_dt).total_seconds())
        if age_seconds < 0:
            raise error_type(
                "ECONOMIC_RECEIPT_FUTURE",
                "economic receipt is from the future",
            )
        if age_seconds > max_age:
            raise error_type(
                "ECONOMIC_RECEIPT_STALE",
                "economic receipt is stale",
            )
    
        decision = receipt.get("decision")
        if decision not in supported_decisions:
            raise error_type(
                "INVALID_ECONOMIC_RECEIPT",
                "economic receipt decision is unsupported",
            )
    
        authority = receipt.get("authority")
        if type(authority) is not dict or frozenset(authority) != expected_authority_keys:
            raise error_type(
                "INVALID_ECONOMIC_AUTHORITY",
                "economic receipt authority schema is unsupported",
            )
        for key, expected in expected_authority_items:
            if authority.get(key) is not expected:
                raise error_type(
                    "INVALID_ECONOMIC_AUTHORITY",
                    "economic receipt authority value is invalid",
                )
    
        binding = {
            "schema": proof_schema,
            "repo": repo,
            "issue": issue,
            "work_id": work_id,
            "canonical_issue_url": canonical_issue_url,
            "gate_request_bytessha256_field": request_bytes_sha,
            "gate_receipt_bytessha256_field": actual_bytes_sha,
            "gate_receiptsha256_field": receipt_sha,
            "gate_requestsha256_field": request_sha,
            "policysha256_field": receipt_policy_sha,
            "gate_as_of": gate_as_of_text,
            "decision_as_of": decision_as_of_text,
            "age_seconds": age_seconds,
            "max_age_seconds": max_age,
            "decision": decision,
        }
        binding_sha = _canonicalsha256_field(binding)
    
        if decision != "GO":
            raise error_type(
                f"ECONOMICS_{decision}",
                "paid-work economics does not authorize live claim instructions",
            )
    
        return {
            **binding,
            "bindingsha256_field": binding_sha,
            "verified": True,
            "authority": "INTERNAL_CLAIM_INSTRUCTION_ADMISSION_ONLY_NO_EXTERNAL_ACTION",
        }
    

    return verify_claim_economic_receipt


verify_claim_economic_receipt = _build_verify_claim_economic_receipt(
    payoff_context=_payoff_context,
    exact_utc=_exact_utc,
    max_age=_max_age,
    read_bounded_regular=_read_bounded_regular,
    sha256_digest=hashlib.sha256,
    strict_json=_strict_json,
    compile_gate=compile_paid_work_effort_value_gate,
    gate_input_error=PaidWorkGateInputError,
    sha256_field=_sha256,
    canonical_sha256=_canonical_sha256,
    verify_gate_receipt=verify_receipt,
    expected_policy_sha256=_EXPECTED_POLICY_SHA256,
    expected_authority_items=tuple(sorted(_EXPECTED_AUTHORITY.items())),
    supported_decisions=frozenset(_SUPPORTED_DECISIONS),
    error_type=ClaimEconomicAdmissionError,
    proof_schema=PROOF_SCHEMA,
)
del _build_verify_claim_economic_receipt
