# SPDX-License-Identifier: MIT
"""Read BountyHub's public catalog and export existing-preflight targets.

Only allowlisted listing fields and aggregate pledge/claim counts leave this
module. A funded listing is discovery evidence, not our assignment or payment.
Named-file checkpoint examples: docs/BOUNTYHUB_CATALOG_OUTPUT.md.
"""

from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

import requests

from concierge.submission_packet import validate_submission_target
from concierge.bountyhub_exclusions import normalize_exclusions, unique_exclusion_fields

API = "https://api.bountyhub.dev/api/bounties"
SCHEMA = "bountyhub-catalog/v1"
_REFRESH_SCHEMA = "bountyhub-target-refresh/v1"
_MONEY = re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,2})?\Z")
_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+\Z")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit_json(value: Any, destination: Path | None) -> None:
    """Keep stdout compatible or replace a named file only after a complete write."""
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if destination is None:
        print(text, end="")
        return
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as output:
            temporary = output.name
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except OSError as exc:
        raise ValueError("cannot write the output file") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                # A failed cleanup must not obscure the write failure or interrupt.
                pass


def _amount(value: Any) -> Decimal:
    if not isinstance(value, str) or not _MONEY.fullmatch(value):
        raise ValueError("amount must be a nonnegative bounded decimal string")
    return Decimal(value)


def _money(value: Decimal) -> str:
    return format(value, ".2f")


def _boolean(record: dict[str, Any], key: str) -> bool:
    value = record.get(key)
    if type(value) is not bool:
        raise ValueError(f"invalid {key} flag")
    return value


def _row(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("listing must be an object")
    listing_id, repo, number = value.get("id"), value.get("repositoryFullName"), value.get("issueNumber")
    if not isinstance(listing_id, str) or not _ID.fullmatch(listing_id):
        raise ValueError("invalid listing identity")
    if not isinstance(repo, str) or not _REPO.fullmatch(repo) or repo.split("/")[1] in {".", ".."}:
        raise ValueError("invalid repository identity")
    if type(number) is not int or number < 1:
        raise ValueError("invalid issue number")
    issue_url = f"https://github.com/{repo}/issues/{number}"
    if not isinstance(value.get("htmlURL"), str) or value["htmlURL"].casefold() != issue_url.casefold():
        raise ValueError("listing issue identities disagree")
    title, state, assignment = value.get("title"), value.get("issueState"), value.get("assignmentType")
    if not all(isinstance(item, str) and item for item in (title, state, assignment)):
        raise ValueError("missing listing metadata")
    row = {
        "listing_id": listing_id, "source_url": f"{API}/{listing_id}",
        "repo": repo, "number": number, "issue_url": issue_url,
        "title": title[:2048], "issue_state": state.lower(),
        "assignment_type": assignment[:64], "has_assignee": value.get("assignee") is not None,
        "claimed": _boolean(value, "claimed"), "retracted": _boolean(value, "retracted"),
        "solved": _boolean(value, "solved"), "frozen": _boolean(value, "isFrozen"),
        "deleted": value.get("deletedAt") is not None,
        "advertised_total_usd": _money(_amount(value.get("totalAmount"))),
        "funding_status": "NOT_REQUESTED", "reported_funded_usd": None,
        "reported_promised_usd": None, "other_payment_status_usd": None,
        "payout_marked_usd": None, "active_pledge_count": None,
        "claim_count": None, "open_claim_count": None,
    }
    language = value.get("language")
    if isinstance(language, str) and language.strip():
        row["language"] = language.strip()[:128]
    return row


def _active(row: dict[str, Any]) -> bool:
    return row.get("issue_state") == "open" and all(
        row.get(key) is False for key in ("claimed", "retracted", "solved", "frozen", "deleted")
    )


def _detail(value: Any, expected: dict[str, Any]) -> dict[str, Any]:
    row = _row(value)
    if "language" not in value and isinstance(expected.get("language"), str):
        row["language"] = expected["language"][:128]
    if (row["listing_id"], row["repo"].casefold(), row["number"]) != (
        expected["listing_id"], expected["repo"].casefold(), expected["number"]
    ):
        raise ValueError("detail belongs to a different listing or issue")
    pledges, claims = value.get("pledges"), value.get("claims")
    if not isinstance(pledges, list) or not isinstance(claims, list):
        raise ValueError("detail lacks pledge or claim records")
    funded = promised = other = payout = Decimal(0)
    active_count = 0
    for pledge in pledges:
        if not isinstance(pledge, dict):
            raise ValueError("invalid pledge")
        if _boolean(pledge, "retracted") or pledge.get("deletedAt") is not None:
            continue
        amount = _amount(pledge.get("amount"))
        status = pledge.get("paymentStatus")
        if not isinstance(status, str) or not status:
            raise ValueError("missing pledge payment status")
        active_count += 1
        if status == "PAID":
            funded += amount
        elif status == "PROMISED":
            promised += amount
        else:
            other += amount
        if _boolean(pledge, "isPaid"):
            payout += amount
    # The pledge array already includes the creator's original pledge. Adding
    # listing.amount again double-counts it; amountPaid also includes fees.
    if funded + promised + other != _amount(row["advertised_total_usd"]):
        raise ValueError("pledge total disagrees with the listing total")
    if any(not isinstance(claim, dict) for claim in claims):
        raise ValueError("invalid claim record")
    current_claims = [claim for claim in claims if claim.get("deletedAt") is None]
    row.update(
        funding_status="COMPLETE", reported_funded_usd=_money(funded),
        reported_promised_usd=_money(promised), other_payment_status_usd=_money(other),
        payout_marked_usd=_money(payout), active_pledge_count=active_count,
        claim_count=len(current_claims),
        open_claim_count=sum(_boolean(claim, "isOpen") and claim.get("rejectedAt") is None
                             for claim in current_claims),
    )
    return row


def _retry_after(value: str | None) -> int | None:
    if not value:
        return None
    if value.isascii() and value.isdigit() and len(value) <= 12:
        return int(value)
    try:
        instant = parsedate_to_datetime(value)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        return max(0, math.ceil((instant - datetime.now(timezone.utc)).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


class _ReadFailure(Exception):
    def __init__(self, code: str, status: int | None = None, retry_after: int | None = None):
        self.code, self.status, self.retry_after = code, status, retry_after


def _get(session: Any, url: str, report: dict[str, Any], **params: Any) -> Any:
    request_limit = report.get("request_limit")
    if request_limit is None and "requested_listing_ids" in report:
        request_limit = len(report["requested_listing_ids"])
    prepared = None
    redirects = 0
    while True:
        if request_limit is not None and report["requests_made"] >= request_limit:
            raise _ReadFailure("REQUEST_LIMIT")
        report["requests_made"] += 1
        try:
            if prepared is None:
                response = session.get(url, params=params or None, timeout=20, allow_redirects=False)
            else:
                settings = session.merge_environment_settings(prepared.url, {}, None, None, None)
                response = session.send(prepared, timeout=20, allow_redirects=False, **settings)
        except requests.RequestException as exc:
            # Response hooks may raise before Session.get returns. Preserve the
            # same status/retry metadata and cleanup as a returned HTTP failure.
            response = exc.response if isinstance(exc, requests.HTTPError) else None
            if response is None:
                raise _ReadFailure(type(exc).__name__) from None
            try:
                raise _ReadFailure("HTTP_ERROR", response.status_code,
                                   _retry_after(response.headers.get("Retry-After"))) from None
            finally:
                response.close()
        try:
            delay = _retry_after(response.headers.get("Retry-After"))
            status = response.status_code
            if response.is_redirect and response.next is not None:
                if delay is not None and delay > 0:
                    raise _ReadFailure("HTTP_ERROR", status, delay)
                if redirects >= session.max_redirects:
                    raise _ReadFailure("TooManyRedirects", status)
                # Requests prepares this hop with its normal auth/cookie
                # stripping. Count and bound the send before following it.
                prepared = response.next
                redirects += 1
                continue
            if status != 200:
                raise _ReadFailure("HTTP_ERROR", status, delay)
            try:
                return response.json()
            except ValueError:
                raise _ReadFailure("INVALID_JSON", status) from None
        finally:
            response.close()


def _submission_target_map(value: Any) -> dict[tuple[str, int], dict[str, str]]:
    """Validate explicit delivery records, keyed by the original bounty issue."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("submission target map must be an owner/repo#number object")
    result: dict[tuple[str, int], dict[str, str]] = {}
    for issue, target in value.items():
        if not isinstance(issue, str):
            raise ValueError("invalid submission target map issue")
        repo, separator, number = issue.rpartition("#")
        if not separator or not _REPO.fullmatch(repo) or not re.fullmatch(r"[1-9][0-9]*", number):
            raise ValueError("submission target map keys must be owner/repo#number")
        key = repo.casefold(), int(number)
        if key in result:
            raise ValueError("duplicate issue identity in submission target map")
        result[key] = validate_submission_target(target, f"https://github.com/{repo}/issues/{number}")
    return result


def _unique_target_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field in submission target map")
        result[key] = value
    return result


def _refresh_target_source(report: dict[str, Any]) -> dict[str, Any]:
    """Expose only completed records from an explicitly bounded refresh receipt."""
    requested, records = report.get("requested_listing_ids"), report.get("records")
    if (report.get("scope") != "requested_listing_ids_only"
            or report.get("catalog_refreshed") is not False
            or not isinstance(requested, list) or not 1 <= len(requested) <= 100
            or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in requested)
            or len(set(requested)) != len(requested)
            or not isinstance(records, list) or len(records) != len(requested)):
        raise ValueError("invalid retained refresh scope")
    started, completed = _instant(report.get("started_at")), _instant(report.get("completed_at"))
    source_started = _instant(report.get("source_started_at"))
    source_completed = _instant(report.get("source_completed_at"))
    observed = _instant(report.get("catalog_observed_through"))
    if not source_started <= observed <= source_completed <= started <= completed:
        raise ValueError("invalid retained refresh observation interval")
    rows = []
    all_complete = True
    for listing_id, record in zip(requested, records):
        if not isinstance(record, dict) or record.get("listing_id") != listing_id:
            raise ValueError("refresh records disagree with requested listing identities")
        repo, number = record.get("repo"), record.get("number")
        if (not isinstance(repo, str) or not _REPO.fullmatch(repo)
                or repo.split("/")[1] in {".", ".."}
                or type(number) is not int or number < 1
                or record.get("source_url") != f"{API}/{listing_id}"):
            raise ValueError("invalid retained refresh record identity")
        status, row = record.get("status"), record.get("listing")
        if status == "NOT_ATTEMPTED":
            if record.get("started_at") is not None or record.get("completed_at") is not None:
                raise ValueError("unattempted refresh record has observation times")
        else:
            row_started = _instant(record.get("started_at"))
            row_completed = _instant(record.get("completed_at"))
            if not started <= row_started <= row_completed <= completed:
                raise ValueError("invalid retained refresh record interval")
        if status == "COMPLETE":
            if (not isinstance(row, dict) or row.get("funding_status") != "COMPLETE"
                    or row.get("listing_id") != listing_id
                    or not isinstance(row.get("repo"), str)
                    or row["repo"].casefold() != repo.casefold()
                    or row.get("number") != number
                    or row.get("source_url") != record["source_url"]):
                raise ValueError("refresh detail disagrees with its requested record")
            rows.append(row)
        elif status in ("READ_FAILED", "INVALID_DETAIL", "NOT_ATTEMPTED") and row is None:
            all_complete = False
        else:
            raise ValueError("invalid retained refresh record outcome")
    if _boolean(report, "details_complete") != all_complete:
        raise ValueError("refresh coverage disagrees with retained records")
    complete = _boolean(report, "complete")
    if complete and not all_complete:
        raise ValueError("partial refresh cannot claim complete coverage")
    return {
        "schema": _REFRESH_SCHEMA, "listings": rows, "complete": complete,
        "started_at": report["started_at"], "completed_at": report["completed_at"],
        "catalog_observed_through": report["catalog_observed_through"],
        "source_schema": _REFRESH_SCHEMA, "source_scope": report["scope"],
        "requested_listing_count": len(requested),
        "source_catalog_started_at": report["source_started_at"],
        "source_catalog_completed_at": report["source_completed_at"],
    }


def select_targets(report: dict[str, Any], minimum_funded_usd: str = "15.00", *,
                   include_promised: bool = False,
                   submission_targets: dict[str, Any] | None = None,
                   excluded_issues: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reduce a retained report without refreshing its time or making requests."""
    if isinstance(report, dict) and report.get("schema") == _REFRESH_SCHEMA:
        report = _refresh_target_source(report)
    elif not isinstance(report, dict) or report.get("schema") != SCHEMA or not isinstance(report.get("listings"), list):
        raise ValueError("expected a bountyhub catalog or target-refresh report")
    delivery = _submission_target_map(submission_targets)
    exclusions = normalize_exclusions(excluded_issues)
    floor = _amount(minimum_funded_usd)
    targets: dict[tuple[str, int], dict[str, Any]] = {}
    associations: dict[str, list[str]] = {}
    unresolved = 0
    for row in report["listings"]:
        if not isinstance(row, dict):
            raise ValueError("invalid retained listing")
        if not _active(row):
            continue
        if _amount(row["advertised_total_usd"]) < floor:
            continue
        if row.get("funding_status") != "COMPLETE":
            unresolved += 1
            continue
        # Never treat reported prior payout as funding available for new work.
        if _amount(row["payout_marked_usd"]) != 0 or _amount(row["other_payment_status_usd"]) != 0:
            unresolved += 1
            continue
        reward = _amount(row["reported_funded_usd"])
        if include_promised:
            reward += _amount(row["reported_promised_usd"])
        if reward < floor:
            continue
        repo, number, listing_id = row.get("repo"), row.get("number"), row.get("listing_id")
        if (not isinstance(repo, str) or not _REPO.fullmatch(repo)
                or type(number) is not int or number < 1
                or not isinstance(listing_id, str) or not _ID.fullmatch(listing_id)):
            raise ValueError("invalid retained target identity")
        key = (repo.casefold(), number)
        target = targets.setdefault(key, {"repo": repo, "number": number})
        if key in delivery:
            target["submission_target"] = delivery[key]
        associations.setdefault(f"{repo.casefold()}#{number}", []).append(listing_id)
    excluded = []
    for key in list(targets):
        if key not in exclusions:
            continue
        target = targets.pop(key)
        excluded.append({
            **target, **exclusions[key],
            "listing_ids": associations.pop(f"{key[0]}#{key[1]}"),
        })
    result = {
        "targets": list(targets.values()), "listing_ids_by_issue": associations,
        "minimum_funded_usd": _money(floor), "unresolved_funding_count": unresolved,
        "source_started_at": report.get("started_at"),
        "source_completed_at": report.get("completed_at"),
        "source_complete": report.get("complete") is True and unresolved == 0,
    }
    if "catalog_observed_through" in report:
        result["catalog_observed_through"] = report["catalog_observed_through"]
        result["catalog_refreshed"] = False
    if report["schema"] == _REFRESH_SCHEMA:
        result.update({key: report[key] for key in (
            "source_schema", "source_scope", "requested_listing_count",
            "source_catalog_started_at", "source_catalog_completed_at",
        )})
    if include_promised:
        del result["minimum_funded_usd"]
        result.update(reward_basis="reported_funded_plus_promised", minimum_reward_usd=_money(floor))
    if excluded_issues is not None:
        result["excluded_targets"] = excluded
    return result


def _record_failure(report: dict[str, Any], exc: _ReadFailure, phase: str, **identity: Any) -> None:
    report["errors"].append({"phase": phase, **identity, "code": exc.code, "http_status": exc.status})
    report["rate_limited"] = exc.status == 429 or (exc.status == 403 and exc.retry_after is not None)
    report["retry_after_seconds"] = exc.retry_after


def _fill_details(session: Any, report: dict[str, Any], floor: Decimal, max_details: int,
                  *, stopped: bool = False, skip_complete: bool = False) -> None:
    detail_attempts = 0
    report["details_complete"] = True
    failed_statuses = {"READ_FAILED", "INVALID_DETAIL"}
    order = list(range(len(report["listings"])))
    if skip_complete:
        # Spend a resumed budget on untouched details before retrying failures.
        # Sorting indices leaves retained listing order and identity unchanged.
        order.sort(key=lambda index: report["listings"][index]["funding_status"] in failed_statuses)
    for index in order:
        row = report["listings"][index]
        if (not _active(row) or _amount(row["advertised_total_usd"]) < floor
                or (skip_complete and row["funding_status"] == "COMPLETE")):
            continue
        if stopped or detail_attempts >= max_details:
            # A deferred failure is not an unattempted row on the next resume.
            if row["funding_status"] not in failed_statuses:
                row["funding_status"] = "NOT_ATTEMPTED" if stopped else "DETAIL_LIMIT"
            report["details_complete"] = False
            continue
        try:
            detail_attempts += 1
            payload = _get(session, row["source_url"], report)
            report["details_fetched"] += 1
            report["listings"][index] = _detail(payload, row)
        except _ReadFailure as exc:
            row["funding_status"] = "READ_FAILED"
            _record_failure(report, exc, "detail", listing_id=row["listing_id"])
            report["details_complete"] = False
            # A listing removed after catalog collection does not invalidate
            # later listings. Its failed read still consumes the detail budget.
            # Respect a requested cooldown even for a missing listing.
            stopped = (
                (exc.retry_after is not None and exc.retry_after > 0)
                or exc.code != "HTTP_ERROR" or exc.status not in {404, 410}
            )
        except ValueError:
            row["funding_status"] = "INVALID_DETAIL"
            report["errors"].append({"phase": "detail", "listing_id": row["listing_id"], "code": "INVALID_DETAIL"})
            report["details_complete"] = False


def fetch_catalog(*, max_pages: int = 10, max_details: int = 50, page_size: int = 100,
                  minimum_total_usd: str = "15.00", include_promised: bool = False,
                  session: Any = None) -> dict[str, Any]:
    """Collect once with bounded reads; retain partial progress and never retry."""
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ValueError("max_pages must be between 1 and 100")
    if type(max_details) is not int or not 0 <= max_details <= 100:
        raise ValueError("max_details must be between 0 and 100")
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")
    floor = _amount(minimum_total_usd)
    if session is None:
        with requests.Session() as owned:
            return fetch_catalog(max_pages=max_pages, max_details=max_details,
                                 page_size=page_size, minimum_total_usd=minimum_total_usd,
                                 include_promised=include_promised, session=owned)
    report: dict[str, Any] = {
        "schema": SCHEMA, "source_url": API, "started_at": _now(),
        "completed_at": None, "complete": False, "catalog_complete": False,
        "details_complete": True, "pages_fetched": 0, "details_fetched": 0,
        "requests_made": 0, "minimum_total_usd": _money(floor),
        "request_limit": max_pages + max_details,
        "max_pages": max_pages, "max_details": max_details, "page_size": page_size,
        "rate_limited": False,
        "retry_after_seconds": None, "errors": [], "listings": [],
    }
    seen: set[str] = set()
    stopped = False

    for page in range(1, max_pages + 1):
        try:
            payload = _get(session, API, report, page=page, limit=page_size)
            if (not isinstance(payload, dict) or not isinstance(payload.get("data"), list)
                    or type(payload.get("hasNextPage")) is not bool):
                raise ValueError("invalid catalog page")
            report["pages_fetched"] += 1
            for raw in payload["data"]:
                row = _row(raw)
                if row["listing_id"] in seen:
                    raise ValueError("listing repeated during pagination")
                seen.add(row["listing_id"])
                report["listings"].append(row)
            if not payload["hasNextPage"]:
                report["catalog_complete"] = True
                break
            if not payload["data"]:
                raise ValueError("empty page reports a successor")
        except _ReadFailure as exc:
            _record_failure(report, exc, "catalog", page=page)
            stopped = True
            break
        except ValueError:
            report["errors"].append({"phase": "catalog", "page": page, "code": "INVALID_PAGE"})
            stopped = True
            break
    if not report["catalog_complete"] and not stopped:
        report["errors"].append({"phase": "catalog", "code": "PAGE_LIMIT"})
    _fill_details(session, report, floor, max_details, stopped=stopped)
    report["completed_at"] = _now()
    report["complete"] = report["catalog_complete"] and report["details_complete"]
    report["listing_count"] = len(report["listings"])
    report["shortlist"] = select_targets(report, minimum_total_usd, include_promised=include_promised)
    return report


def _instant(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing observation timestamp")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("invalid observation timestamp") from None
    if instant.tzinfo is None:
        raise ValueError("observation timestamp must have a timezone")
    return instant


def _resume_input(snapshot: Any) -> dict[str, Any]:
    """Validate every retained identity before opening a session; export no raw extras."""
    if (not isinstance(snapshot, dict) or snapshot.get("schema") != SCHEMA
            or snapshot.get("source_url") != API or snapshot.get("catalog_complete") is not True
            or not isinstance(snapshot.get("listings"), list) or len(snapshot["listings"]) > 10000):
        raise ValueError("resume requires a fully traversed bountyhub-catalog/v1 report")
    started, completed = _instant(snapshot.get("started_at")), _instant(snapshot.get("completed_at"))
    observed = snapshot.get("catalog_observed_through", snapshot["completed_at"])
    if not started <= _instant(observed) <= completed:
        raise ValueError("invalid observation interval")
    result = {key: snapshot[key] for key in (
        "schema", "source_url", "started_at", "completed_at", "minimum_total_usd",
        "max_pages", "max_details", "pages_fetched", "details_fetched", "requests_made",
    )}
    for key in ("max_pages", "max_details", "pages_fetched", "details_fetched", "requests_made"):
        if type(result[key]) is not int or result[key] < 0:
            raise ValueError("invalid retained count")
    # Early v1 reports did not record page_size. Resuming reads no catalog
    # pages, so retain that absence rather than invent a historical value.
    if "page_size" in snapshot:
        page_size = snapshot["page_size"]
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("invalid retained page size")
        result["page_size"] = page_size
    if (not 1 <= result["max_pages"] <= 100 or not 0 <= result["max_details"] <= 100
            or not 1 <= result["pages_fetched"] <= result["max_pages"]
            or result["requests_made"] < result["pages_fetched"] + result["details_fetched"]):
        raise ValueError("invalid retained collection bounds")
    _amount(result["minimum_total_usd"])
    for key in ("complete", "details_complete", "rate_limited"):
        result[key] = _boolean(snapshot, key)
    retry = snapshot.get("retry_after_seconds")
    if retry is not None and (type(retry) is not int or not 0 <= retry < 10**12):
        raise ValueError("invalid retained retry guidance")
    if (not isinstance(snapshot.get("errors"), list)
            or any(not isinstance(error, dict) or error.get("phase") != "detail"
                   for error in snapshot["errors"])):
        raise ValueError("retained catalog contains invalid or unresolved catalog errors")
    # Only the current attempt's errors are emitted. Keep the source snapshot:
    # the resume receipt binds it by digest and records its original error count.
    result.update(catalog_complete=True, catalog_observed_through=observed,
                  retry_after_seconds=retry, errors=[], listings=[])
    seen: set[str] = set()
    for saved in snapshot["listings"]:
        if not isinstance(saved, dict):
            raise ValueError("invalid retained listing")
        row = _row({
            "id": saved.get("listing_id"), "repositoryFullName": saved.get("repo"),
            "issueNumber": saved.get("number"), "htmlURL": saved.get("issue_url"),
            "title": saved.get("title"), "issueState": saved.get("issue_state"),
            "assignmentType": saved.get("assignment_type"),
            "assignee": {} if _boolean(saved, "has_assignee") else None,
            "claimed": saved.get("claimed"), "retracted": saved.get("retracted"),
            "solved": saved.get("solved"), "isFrozen": saved.get("frozen"),
            "deletedAt": "retained" if _boolean(saved, "deleted") else None,
            "totalAmount": saved.get("advertised_total_usd"), "language": saved.get("language"),
        })
        if row["source_url"] != saved.get("source_url") or row["listing_id"] in seen:
            raise ValueError("invalid or repeated retained listing source")
        seen.add(row["listing_id"])
        status = saved.get("funding_status")
        if not isinstance(status, str) or status not in {
            "COMPLETE", "NOT_REQUESTED", "NOT_ATTEMPTED", "DETAIL_LIMIT", "READ_FAILED", "INVALID_DETAIL"
        }:
            raise ValueError("invalid retained funding status")
        row["funding_status"] = status
        if status == "COMPLETE":
            money_keys = ("reported_funded_usd", "reported_promised_usd", "other_payment_status_usd", "payout_marked_usd")
            amounts = [_amount(saved.get(key)) for key in money_keys]
            if sum(amounts[:3]) != _amount(row["advertised_total_usd"]) or amounts[3] > sum(amounts[:3]):
                raise ValueError("invalid retained funding totals")
            row.update(zip(money_keys, map(_money, amounts)))
            for key in ("active_pledge_count", "claim_count", "open_claim_count"):
                if type(saved.get(key)) is not int or saved[key] < 0:
                    raise ValueError("invalid retained pledge or claim count")
                row[key] = saved[key]
            if row["open_claim_count"] > row["claim_count"]:
                raise ValueError("invalid retained open claim count")
        result["listings"].append(row)
    if type(snapshot.get("listing_count")) is not int or snapshot["listing_count"] != len(seen):
        raise ValueError("retained listing count disagrees")
    result["listing_count"] = len(seen)
    return result


def resume_catalog(snapshot: dict[str, Any], *, max_details: int = 50,
                   minimum_total_usd: str | None = None, session: Any = None) -> dict[str, Any]:
    """Finish unresolved detail reads, not a fresh catalog or eligibility check."""
    if type(max_details) is not int or not 0 <= max_details <= 100:
        raise ValueError("max_details must be between 0 and 100")
    report = _resume_input(snapshot)
    shortlist = snapshot.get("shortlist")
    if not isinstance(shortlist, dict):
        raise ValueError("missing retained shortlist scope")
    basis = shortlist.get("reward_basis")
    if basis is not None and basis != "reported_funded_plus_promised":
        raise ValueError("invalid retained reward basis")
    include_promised = basis is not None
    source_minimum = report["minimum_total_usd"]
    floor_key = "minimum_reward_usd" if include_promised else "minimum_funded_usd"
    if _amount(shortlist.get(floor_key)) != _amount(source_minimum):
        raise ValueError("retained shortlist floor disagrees with collection")
    # Every listing summary was retained, including those below the old floor.
    # Changing the detail scope explicitly does not refresh those observations.
    minimum = source_minimum if minimum_total_usd is None else _money(_amount(minimum_total_usd))
    floor = _amount(minimum)
    report["minimum_total_usd"] = minimum
    try:
        source_sha256 = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                                                 allow_nan=False).encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        raise ValueError("retained catalog must contain finite JSON values") from None
    pending = any(_active(row) and _amount(row["advertised_total_usd"]) >= floor
                  and row["funding_status"] != "COMPLETE" for row in report["listings"])
    started_at = _now()
    previous_requests, previous_details = report["requests_made"], report["details_fetched"]
    if pending and max_details:
        if _instant(started_at) < _instant(report["completed_at"]):
            raise ValueError("resume time predates retained observation")
        if report["retry_after_seconds"] is not None:
            elapsed = (_instant(started_at) - _instant(report["completed_at"])).total_seconds()
            if elapsed < report["retry_after_seconds"]:
                raise ValueError("retained Retry-After cooldown has not elapsed")
        report.update(max_details=max_details, request_limit=previous_requests + max_details,
                      rate_limited=False, retry_after_seconds=None)
        if session is None:
            with requests.Session() as owned:
                _fill_details(owned, report, floor, max_details, skip_complete=True)
        else:
            _fill_details(session, report, floor, max_details, skip_complete=True)
        report["completed_at"] = _now()
    else:
        # A no-op must not turn old observations into a newly dated capture or
        # clear a cooldown. Even max_details=0 retains the last observation end.
        report["details_complete"] = not pending
    report["complete"] = report["details_complete"]
    report["resume"] = {
        "started_at": started_at, "completed_at": _now(), "catalog_refreshed": False,
        "source_started_at": snapshot["started_at"], "source_completed_at": snapshot["completed_at"],
        "source_error_count": len(snapshot["errors"]), "source_sha256": source_sha256,
        "requests_made": report["requests_made"] - previous_requests,
        "details_fetched": report["details_fetched"] - previous_details,
    }
    if minimum_total_usd is not None:
        report["resume"].update(source_minimum_total_usd=source_minimum,
                                minimum_total_usd=minimum,
                                floor_changed=minimum != source_minimum)
    report["shortlist"] = select_targets(report, minimum, include_promised=include_promised)
    return report


def _input_error_message(exc: Exception) -> str:
    """Explain input failures without echoing file contents or input paths."""
    if isinstance(exc, json.JSONDecodeError):
        return f"invalid JSON at line {exc.lineno}, column {exc.colno}"
    if isinstance(exc, OSError):
        return "cannot read the input file"
    if isinstance(exc, KeyError):
        return "missing a required catalog field"
    return str(exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help="Read the public catalog and selected pledge details")
    collect.add_argument("--max-pages", type=int, default=10)
    collect.add_argument("--max-details", type=int, default=50)
    collect.add_argument("--page-size", type=int, default=100)
    targets = commands.add_parser("targets", help="Export targets from a retained catalog or refresh with no provider reads")
    targets.add_argument("snapshot", type=Path)
    targets.add_argument(
        "--submission-target-map", type=Path,
        help="Optional JSON owner/repo#number map of existing source-bound submission_target records",
    )
    targets.add_argument(
        "--exclude-issues", type=Path,
        help="Optional JSON owner/repo#number map of retained reasons, source URLs and observation times",
    )
    resume = commands.add_parser("resume", help="Finish unresolved details from a retained complete catalog")
    resume.add_argument("snapshot", type=Path)
    resume.add_argument("--max-details", type=int, default=50)
    resume.add_argument(
        "--min-funded-usd", "--min-reward-usd", dest="min_funded_usd",
        help="Explicit detail/shortlist floor; default preserves the retained floor. "
             "The retained funded or funded-plus-promised reward basis is unchanged.",
    )
    refresh = commands.add_parser("refresh", help="Refresh explicit known listing details without catalog pages")
    refresh.add_argument("snapshot", type=Path)
    refresh.add_argument("--listing-id", action="append", required=True, dest="listing_ids")
    refresh.add_argument("--previous-refresh", type=Path,
                         help="Last targeted-refresh receipt; enforces its Retry-After cooldown")
    for command in (collect, targets):
        command.add_argument(
            "--min-funded-usd", "--min-reward-usd", dest="min_funded_usd", default="15.00",
            help="Minimum USD in the selected reward basis: funded by default, funded plus promised "
                 "with --include-promised (default: 15.00)",
        )
        command.add_argument(
            "--include-promised", action="store_true",
            help="Include resolved PROMISED pledges with funded pledges when applying the reward floor",
        )
    for command in commands.choices.values():
        command.add_argument(
            "--output", type=Path,
            help="Atomically replace this JSON file instead of writing to stdout; parent must exist",
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            result = fetch_catalog(max_pages=args.max_pages, max_details=args.max_details,
                                   page_size=args.page_size,
                                   minimum_total_usd=args.min_funded_usd,
                                   include_promised=args.include_promised)
            complete = result["shortlist"]["source_complete"]
        elif args.command == "refresh":
            from concierge.bountyhub_refresh import _load, refresh_listings
            result = refresh_listings(
                _load(args.snapshot), args.listing_ids,
                previous_refresh=_load(args.previous_refresh) if args.previous_refresh else None,
            )
            complete = result["complete"]
        elif args.command == "resume":
            with args.snapshot.open("rb") as source:
                raw = source.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("retained catalog exceeds 4 MiB")
            result = resume_catalog(json.loads(raw), max_details=args.max_details,
                                    minimum_total_usd=args.min_funded_usd)
            complete = result["shortlist"]["source_complete"]
        else:
            submission_targets = None
            excluded_issues = None
            if args.exclude_issues is not None:
                with args.exclude_issues.open("rb") as source:
                    raw = source.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError("issue exclusions exceed 1 MiB")
                excluded_issues = json.loads(raw, object_pairs_hook=unique_exclusion_fields)
                if excluded_issues is None:
                    raise ValueError("issue exclusions must be an object")
            if args.submission_target_map is not None:
                with args.submission_target_map.open("rb") as source:
                    raw = source.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError("submission target map exceeds 1 MiB")
                submission_targets = json.loads(raw, object_pairs_hook=_unique_target_fields)
                if submission_targets is None:
                    raise ValueError("submission target map must be an object")
            with args.snapshot.open(encoding="utf-8") as source:
                result = select_targets(json.load(source), args.min_funded_usd,
                                        include_promised=args.include_promised,
                                        submission_targets=submission_targets,
                                        excluded_issues=excluded_issues)
            complete = result["source_complete"]
        if args.command == "targets":
            # The existing batch preflight accepts this exact envelope. Keep
            # observation/funding evidence in the original catalog report.
            _emit_json({"candidates": result["targets"]}, args.output)
            basis = (
                f" reward_basis={result['reward_basis']} minimum_reward_usd={result['minimum_reward_usd']}"
                if args.include_promised else ""
            )
            if "catalog_observed_through" in result:
                basis += f" catalog_refreshed=false catalog_observed_through={result['catalog_observed_through']}"
            if "source_scope" in result:
                basis += (f" source_scope={result['source_scope']}"
                          f" requested_listing_count={result['requested_listing_count']}"
                          f" source_catalog_started_at={result['source_catalog_started_at']}"
                          f" source_catalog_completed_at={result['source_catalog_completed_at']}")
            if args.submission_target_map is not None:
                basis += f" mapped_submission_targets={sum('submission_target' in row for row in result['targets'])}"
            if args.exclude_issues is not None:
                basis += f" excluded_targets={len(result['excluded_targets'])}"
                for excluded in result["excluded_targets"]:
                    print("excluded_target=" + json.dumps(excluded, sort_keys=True), file=sys.stderr)
            print(
                f"source_started_at={result['source_started_at']} "
                f"source_completed_at={result['source_completed_at']} "
                f"source_complete={str(complete).lower()} "
                f"candidates={len(result['targets'])}{basis}", file=sys.stderr,
            )
        else:
            _emit_json(result, args.output)
        if not complete:
            selected = result.get("shortlist", result)
            basis = "funded plus promised" if selected.get("reward_basis") else "funded"
            print(f"PARTIAL: retained rows do not establish a complete {basis} shortlist", file=sys.stderr)
        return 0 if complete else 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"bountyhub-catalog: {type(exc).__name__}: {_input_error_message(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
