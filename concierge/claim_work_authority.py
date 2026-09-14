# SPDX-License-Identifier: MIT
"""Bind one sponsor-adjudication claim unit to live merged GitHub work.

This module closes a narrow authority gap between sponsor adjudication and
collection. A sponsor-authenticated reward does not, by itself, prove which
GitHub pull request was merged for that claim unit. Conversely, a caller-written
``state="MERGED"`` flag is not GitHub evidence.

The binding requires two independent facts before it can mint a durable receipt:

1. an already authority-verified sponsor-adjudication report plus an
   out-of-band host HMAC authorizing the exact claim-unit -> work relation; and
2. a fresh, read-only GitHub pull-request readback proving the exact repo/PR/head
   is actually merged.

No GitHub mutation, sponsor contact, payout request, payment action, cash claim,
or revenue recognition occurs here.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping

import requests

from .sponsor_adjudication import AdjudicationError, verify_report


INPUT_SCHEMA = "bounty-claim-work-live-bind-input/v1"
RECEIPT_SCHEMA = "bounty-claim-work-live-binding/v1"
RELATION_AUTH_SCHEMA = "bounty-claim-work-relation-authority/v1"

KEY_ENV = "BOUNTY_CLAIM_WORK_AUTHORITY_KEY_HEX"
PROVIDER_ENV = "BOUNTY_CLAIM_WORK_AUTHORIZED_PROVIDER"
PRINCIPAL_ENV = "BOUNTY_CLAIM_WORK_AUTHORIZED_PRINCIPAL_SHA256"
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"

_MAX_JSON_BYTES = 2 * 1024 * 1024
_MAX_AUTHORITY_AGE_SECONDS = 300
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REPO_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class ClaimWorkAuthorityError(ValueError):
    """Malformed input, failed authority, or failed live GitHub proof."""


def _canonical_json(value: Any) -> bytes:
    try:
        raw = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ClaimWorkAuthorityError("value is not canonical JSON") from exc
    if len(raw) > _MAX_JSON_BYTES:
        raise ClaimWorkAuthorityError("canonical JSON exceeds size limit")
    return raw


def _sha256_obj(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _exact_dict(value: Any, keys: set[str], field: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ClaimWorkAuthorityError(f"{field} must contain exactly {sorted(keys)}")
    return value


def _text(value: Any, field: str, *, max_len: int = 512) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > max_len
        or any(ord(ch) < 32 for ch in value)
    ):
        raise ClaimWorkAuthorityError(f"{field} must be bounded canonical text")
    return value


def _sha256(value: Any, field: str) -> str:
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        raise ClaimWorkAuthorityError(f"{field} must be lowercase SHA-256")
    return value


def _sha40(value: Any, field: str) -> str:
    if type(value) is not str or not _SHA40_RE.fullmatch(value):
        raise ClaimWorkAuthorityError(f"{field} must be lowercase 40-hex")
    return value


def _canonical_utc(value: Any, field: str) -> tuple[str, datetime]:
    text = _text(value, field, max_len=20)
    if not text.endswith("Z"):
        raise ClaimWorkAuthorityError(f"{field} must be canonical UTC")
    try:
        dt = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ClaimWorkAuthorityError(f"{field} must be canonical UTC") from exc
    if dt.strftime("%Y-%m-%dT%H:%M:%SZ") != text:
        raise ClaimWorkAuthorityError(f"{field} must be canonical UTC")
    return text, dt


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _work(value: Any) -> dict[str, Any]:
    obj = _exact_dict(value, {"repo", "pr", "head_sha"}, "work")
    repo = _text(obj["repo"], "work.repo", max_len=200)
    parts = repo.split("/")
    if (
        len(parts) != 2
        or not all(_REPO_PART_RE.fullmatch(part) for part in parts)
        or any(part in {".", ".."} for part in parts)
    ):
        raise ClaimWorkAuthorityError("work.repo must be owner/name")
    pr = obj["pr"]
    if type(pr) is not int or pr <= 0:
        raise ClaimWorkAuthorityError("work.pr must be a positive integer (bool invalid)")
    head_sha = _sha40(obj["head_sha"], "work.head_sha")
    return {"repo": repo, "pr": pr, "head_sha": head_sha}


def _relation_evidence(value: Any) -> dict[str, str]:
    obj = _exact_dict(value, {"ref", "sha256"}, "relation_evidence")
    ref = _text(obj["ref"], "relation_evidence.ref", max_len=1024)
    if not _ID_RE.fullmatch(ref):
        raise ClaimWorkAuthorityError(
            "relation_evidence.ref must be one bounded opaque reference"
        )
    return {"ref": ref, "sha256": _sha256(obj["sha256"], "relation_evidence.sha256")}


def _claim_unit(report: Mapping[str, Any], claim_unit_id: str) -> dict[str, Any]:
    units = report.get("claim_units")
    if type(units) is not list:
        raise ClaimWorkAuthorityError("verified adjudication report lacks claim_units")
    matches = [
        unit
        for unit in units
        if type(unit) is dict and unit.get("claim_unit_id") == claim_unit_id
    ]
    if len(matches) != 1:
        raise ClaimWorkAuthorityError(
            "claim_unit_id must select exactly one verified sponsor claim unit"
        )
    unit = matches[0]
    if unit.get("status") == "duplicate" or unit.get("collapsed_into") is not None:
        raise ClaimWorkAuthorityError(
            "claim_unit_id must select the canonical non-collapsed claim unit"
        )
    finding_ids = unit.get("finding_ids")
    submission_ids = unit.get("submission_ids")
    if (
        type(finding_ids) is not list
        or not finding_ids
        or len(finding_ids) != len(set(finding_ids))
        or type(submission_ids) is not list
        or len(submission_ids) != len(set(submission_ids))
    ):
        raise ClaimWorkAuthorityError("verified claim-unit membership is malformed")
    checked_findings: list[str] = []
    checked_submissions: list[str] = []
    for field, source, out in (
        ("finding_ids", finding_ids, checked_findings),
        ("submission_ids", submission_ids, checked_submissions),
    ):
        for item in source:
            ident = _text(item, f"claim_unit.{field}", max_len=128)
            if not _ID_RE.fullmatch(ident):
                raise ClaimWorkAuthorityError(
                    f"verified claim_unit.{field} contains invalid identifier"
                )
            out.append(ident)
    return {
        "claim_unit_id": claim_unit_id,
        "status": _text(unit.get("status"), "claim_unit.status", max_len=64),
        "finding_ids": sorted(checked_findings),
        "submission_ids": sorted(checked_submissions),
    }


def _verified_context(
    report_raw: Any,
    claim_unit_id_raw: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    if type(report_raw) is not dict:
        raise ClaimWorkAuthorityError("adjudication_report must be an object")
    try:
        report = verify_report(report_raw)
    except AdjudicationError as exc:
        raise ClaimWorkAuthorityError(
            "adjudication_report failed host-authority verification"
        ) from exc
    claim_unit_id = _text(claim_unit_id_raw, "claim_unit_id", max_len=128)
    if not _ID_RE.fullmatch(claim_unit_id):
        raise ClaimWorkAuthorityError("claim_unit_id is invalid")
    unit = _claim_unit(report, claim_unit_id)
    program = report.get("program")
    if type(program) is not dict:
        raise ClaimWorkAuthorityError("verified report lacks program")
    program_id = _text(program.get("program_id"), "program.program_id", max_len=128)
    sponsor = _text(program.get("sponsor"), "program.sponsor", max_len=512)
    report_sha = _sha256(report.get("report_sha256"), "adjudication_report.report_sha256")
    return report, unit, {
        "program_id": program_id,
        "sponsor": sponsor,
        "report_sha256": report_sha,
    }


def _scope(
    *,
    context: Mapping[str, str],
    unit: Mapping[str, Any],
    work: Mapping[str, Any],
    relation: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "schema": RELATION_AUTH_SCHEMA,
        "adjudication_report_sha256": context["report_sha256"],
        "program_id": context["program_id"],
        "claim_unit": {
            "claim_unit_id": unit["claim_unit_id"],
            "finding_ids": list(unit["finding_ids"]),
            "submission_ids": list(unit["submission_ids"]),
        },
        "work": dict(work),
        "relation_evidence": dict(relation),
    }


def compute_relation_scope(
    adjudication_report: Any,
    claim_unit_id: Any,
    work: Any,
    relation_evidence: Any,
) -> dict[str, Any]:
    """Return the exact non-secret scope an external trusted host must authorize."""
    _report, unit, context = _verified_context(adjudication_report, claim_unit_id)
    normalized_work = _work(work)
    relation = _relation_evidence(relation_evidence)
    scope = _scope(
        context=context,
        unit=unit,
        work=normalized_work,
        relation=relation,
    )
    return {"scope": scope, "scope_sha256": _sha256_obj(scope)}


def _host_key() -> bytes:
    raw = os.environ.get(KEY_ENV)
    if raw is None or not _SHA256_RE.fullmatch(raw):
        raise ClaimWorkAuthorityError(f"{KEY_ENV} must be 64 lowercase hex characters")
    return bytes.fromhex(raw)


def _host_identity() -> tuple[str, str]:
    provider = os.environ.get(PROVIDER_ENV)
    principal = os.environ.get(PRINCIPAL_ENV)
    if provider is None or not _PROVIDER_RE.fullmatch(provider):
        raise ClaimWorkAuthorityError(f"{PROVIDER_ENV} is missing or invalid")
    if principal is None or not _SHA256_RE.fullmatch(principal):
        raise ClaimWorkAuthorityError(f"{PRINCIPAL_ENV} is missing or invalid")
    return provider, principal


def _authority_message(
    provider: str,
    principal_sha256: str,
    captured_at: str,
    scope_sha256: str,
) -> dict[str, str]:
    return {
        "schema": RELATION_AUTH_SCHEMA,
        "provider": provider,
        "principal_sha256": principal_sha256,
        "captured_at": captured_at,
        "scope_sha256": scope_sha256,
    }


def _verify_relation_authority(
    value: Any,
    *,
    scope_sha256: str,
    now: datetime,
) -> dict[str, str]:
    obj = _exact_dict(
        value,
        {
            "provider",
            "principal_sha256",
            "captured_at",
            "scope_sha256",
            "signature_sha256",
        },
        "relation_authority",
    )
    provider = _text(obj["provider"], "relation_authority.provider", max_len=64)
    if not _PROVIDER_RE.fullmatch(provider):
        raise ClaimWorkAuthorityError("relation_authority.provider is invalid")
    principal = _sha256(
        obj["principal_sha256"], "relation_authority.principal_sha256"
    )
    captured_at, captured_dt = _canonical_utc(
        obj["captured_at"], "relation_authority.captured_at"
    )
    supplied_scope = _sha256(
        obj["scope_sha256"], "relation_authority.scope_sha256"
    )
    signature = _sha256(
        obj["signature_sha256"], "relation_authority.signature_sha256"
    )
    expected_provider, expected_principal = _host_identity()
    if provider != expected_provider or principal != expected_principal:
        raise ClaimWorkAuthorityError(
            "relation_authority provider/principal is not host-authorized"
        )
    if supplied_scope != scope_sha256:
        raise ClaimWorkAuthorityError(
            "relation_authority is not bound to this claim/work scope"
        )
    if captured_dt > now:
        raise ClaimWorkAuthorityError("relation_authority capture is in the future")
    age = (now - captured_dt).total_seconds()
    if age > _MAX_AUTHORITY_AGE_SECONDS:
        raise ClaimWorkAuthorityError("relation_authority capture is stale")
    message = _authority_message(
        provider, principal, captured_at, supplied_scope
    )
    expected = hmac.new(_host_key(), _canonical_json(message), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ClaimWorkAuthorityError("relation_authority HMAC is invalid")
    return {
        "provider": provider,
        "principal_sha256": principal,
        "captured_at": captured_at,
        "scope_sha256": supplied_scope,
    }


def _normalize_input(payload: Any) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, str],
    dict[str, Any],
    dict[str, str],
    dict[str, Any],
]:
    root = _exact_dict(
        payload,
        {
            "schema",
            "adjudication_report",
            "claim_unit_id",
            "work",
            "relation_evidence",
            "relation_authority",
        },
        "input",
    )
    if root["schema"] != INPUT_SCHEMA:
        raise ClaimWorkAuthorityError(f"input.schema must be {INPUT_SCHEMA}")
    report, unit, context = _verified_context(
        root["adjudication_report"], root["claim_unit_id"]
    )
    work = _work(root["work"])
    relation = _relation_evidence(root["relation_evidence"])
    scope = _scope(context=context, unit=unit, work=work, relation=relation)
    scope_sha = _sha256_obj(scope)
    now = _now_utc()
    authority = _verify_relation_authority(
        root["relation_authority"], scope_sha256=scope_sha, now=now
    )
    return report, unit, context, work, relation, authority


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "bounty-concierge-claim-work-authority/1",
    }
    token = os.environ.get(GITHUB_TOKEN_ENV)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _live_pull(
    work: Mapping[str, Any],
    *,
    session: Any,
    now: datetime,
) -> dict[str, str | int]:
    repo = work["repo"]
    pr = work["pr"]
    api_url = f"https://api.github.com/repos/{repo}/pulls/{pr}"
    try:
        response = session.get(
            api_url,
            headers=_github_headers(),
            timeout=15,
            allow_redirects=False,
        )
    except Exception as exc:
        raise ClaimWorkAuthorityError("GitHub merge readback failed") from exc
    status = getattr(response, "status_code", None)
    if status != 200:
        raise ClaimWorkAuthorityError("GitHub merge readback was not HTTP 200")
    response_url = getattr(response, "url", api_url)
    if response_url != api_url:
        raise ClaimWorkAuthorityError("GitHub merge readback URL drifted")
    try:
        payload = response.json()
    except Exception as exc:
        raise ClaimWorkAuthorityError("GitHub merge readback was not valid JSON") from exc
    if type(payload) is not dict:
        raise ClaimWorkAuthorityError("GitHub merge readback must be an object")
    expected_html = f"https://github.com/{repo}/pull/{pr}"
    if payload.get("html_url") != expected_html:
        raise ClaimWorkAuthorityError("GitHub pull identity mismatch")
    if type(payload.get("number")) is not int or payload["number"] != pr:
        raise ClaimWorkAuthorityError("GitHub pull number mismatch")
    if payload.get("state") != "closed" or payload.get("merged") is not True:
        raise ClaimWorkAuthorityError("GitHub pull is not merged")
    head = payload.get("head")
    if type(head) is not dict or head.get("sha") != work["head_sha"]:
        raise ClaimWorkAuthorityError("GitHub merged head does not match authorized work")
    base = payload.get("base")
    base_repo = base.get("repo") if type(base) is dict else None
    if type(base_repo) is not dict or base_repo.get("full_name") != repo:
        raise ClaimWorkAuthorityError("GitHub pull base repository identity mismatch")
    merge_commit_sha = _sha40(
        payload.get("merge_commit_sha"), "GitHub merge_commit_sha"
    )
    merged_at, merged_dt = _canonical_utc(
        payload.get("merged_at"), "GitHub merged_at"
    )
    if merged_dt > now:
        raise ClaimWorkAuthorityError("GitHub merged_at is in the future")
    return {
        "repo": repo,
        "pr": pr,
        "canonical_url": expected_html,
        "head_sha": work["head_sha"],
        "merge_commit_sha": merge_commit_sha,
        "merged_at": merged_at,
        "api_url": api_url,
    }


def _receipt_hmac(core: Mapping[str, Any]) -> str:
    domain = b"bounty-claim-work-live-binding/v1\x00"
    return hmac.new(_host_key(), domain + _canonical_json(core), hashlib.sha256).hexdigest()


def _assemble_receipt(
    *,
    unit: Mapping[str, Any],
    context: Mapping[str, str],
    live_work: Mapping[str, Any],
    relation: Mapping[str, str],
    authority: Mapping[str, str],
    observed_at: str,
) -> dict[str, Any]:
    core = {
        "schema": RECEIPT_SCHEMA,
        "disposition": "MERGED_WORK_BOUND",
        "adjudication_report_sha256": context["report_sha256"],
        "program": {
            "program_id": context["program_id"],
            "sponsor": context["sponsor"],
        },
        "claim_unit": {
            "claim_unit_id": unit["claim_unit_id"],
            "status": unit["status"],
            "finding_ids": list(unit["finding_ids"]),
            "submission_ids": list(unit["submission_ids"]),
        },
        "work": dict(live_work),
        "relation_evidence": dict(relation),
        "relation_authority": dict(authority),
        "provider_observed_at": observed_at,
        "authority": {
            "github_read_only": True,
            "claim_work_relation_host_authorized": True,
            "github_mutation": False,
            "sponsor_contact": False,
            "claim_submission": False,
            "payout_requested": False,
            "payment_initiated": False,
            "cash_recognized": False,
            "revenue_recognized": False,
            "collection_send_authorized": False,
        },
    }
    receipt = dict(core)
    receipt["host_receipt_hmac_sha256"] = _receipt_hmac(core)
    receipt["receipt_sha256"] = _sha256_obj(receipt)
    return receipt


def bind_claim_work(
    payload: Any,
    *,
    session: Any = requests,
) -> dict[str, Any]:
    """Mint one durable receipt only after fresh host relation + GitHub merge proof."""
    _report, unit, context, work, relation, authority = _normalize_input(payload)
    now = _now_utc()
    live_work = _live_pull(work, session=session, now=now)
    observed_at = now.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _assemble_receipt(
        unit=unit,
        context=context,
        live_work=live_work,
        relation=relation,
        authority=authority,
        observed_at=observed_at,
    )


_RECEIPT_KEYS = {
    "schema",
    "disposition",
    "adjudication_report_sha256",
    "program",
    "claim_unit",
    "work",
    "relation_evidence",
    "relation_authority",
    "provider_observed_at",
    "authority",
    "host_receipt_hmac_sha256",
    "receipt_sha256",
}


def verify_claim_work_receipt(receipt: Any) -> bool:
    """Verify a retained historical receipt without reapplying current freshness."""
    try:
        if type(receipt) is not dict or set(receipt) != _RECEIPT_KEYS:
            return False
        if receipt.get("schema") != RECEIPT_SCHEMA:
            return False
        if receipt.get("disposition") != "MERGED_WORK_BOUND":
            return False
        supplied_receipt_sha = _sha256(receipt.get("receipt_sha256"), "receipt_sha256")
        payload_without_receipt_sha = {
            key: deepcopy(value)
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
        if _sha256_obj(payload_without_receipt_sha) != supplied_receipt_sha:
            return False
        supplied_hmac = _sha256(
            receipt.get("host_receipt_hmac_sha256"),
            "host_receipt_hmac_sha256",
        )
        core = {
            key: deepcopy(value)
            for key, value in receipt.items()
            if key not in {"host_receipt_hmac_sha256", "receipt_sha256"}
        }
        expected_hmac = _receipt_hmac(core)
        if not hmac.compare_digest(supplied_hmac, expected_hmac):
            return False
        _sha256(
            receipt.get("adjudication_report_sha256"),
            "adjudication_report_sha256",
        )
        _canonical_utc(receipt.get("provider_observed_at"), "provider_observed_at")
        return True
    except (ClaimWorkAuthorityError, TypeError, ValueError):
        return False


def _stable_projection(receipt: Mapping[str, Any]) -> dict[str, Any]:
    relation_authority = receipt.get("relation_authority")
    if type(relation_authority) is not dict:
        raise ClaimWorkAuthorityError("receipt relation_authority is malformed")
    return {
        "schema": receipt.get("schema"),
        "disposition": receipt.get("disposition"),
        "adjudication_report_sha256": receipt.get("adjudication_report_sha256"),
        "program": deepcopy(receipt.get("program")),
        "claim_unit": deepcopy(receipt.get("claim_unit")),
        "work": deepcopy(receipt.get("work")),
        "relation_evidence": deepcopy(receipt.get("relation_evidence")),
        "relation_authority": {
            "provider": relation_authority.get("provider"),
            "principal_sha256": relation_authority.get("principal_sha256"),
            "scope_sha256": relation_authority.get("scope_sha256"),
        },
        "authority": deepcopy(receipt.get("authority")),
    }


def verify_claim_work_current(
    payload: Any,
    receipt: Any,
    *,
    session: Any = requests,
) -> bool:
    """Reacquire current authority/readback and compare the stable bound generation."""
    if not verify_claim_work_receipt(receipt):
        return False
    try:
        fresh = bind_claim_work(payload, session=session)
        return _stable_projection(fresh) == _stable_projection(receipt)
    except ClaimWorkAuthorityError:
        return False


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ClaimWorkAuthorityError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _reject_float(_value: str) -> None:
    raise ClaimWorkAuthorityError("JSON floats are not allowed")


def load_json(path: str | Path) -> Any:
    """Bounded no-follow JSON ingress with duplicate-key and float rejection."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(Path(path), flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ClaimWorkAuthorityError("input must be a regular file")
        if st.st_size > _MAX_JSON_BYTES:
            raise ClaimWorkAuthorityError("input exceeds size limit")
        chunks: list[bytes] = []
        remaining = _MAX_JSON_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > _MAX_JSON_BYTES:
            raise ClaimWorkAuthorityError("input exceeds size limit")
    finally:
        os.close(fd)
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_float,
        )
    except UnicodeDecodeError as exc:
        raise ClaimWorkAuthorityError("input is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ClaimWorkAuthorityError("input is not valid JSON") from exc


__all__ = [
    "ClaimWorkAuthorityError",
    "INPUT_SCHEMA",
    "RECEIPT_SCHEMA",
    "RELATION_AUTH_SCHEMA",
    "compute_relation_scope",
    "bind_claim_work",
    "verify_claim_work_receipt",
    "verify_claim_work_current",
    "load_json",
]
