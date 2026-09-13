# SPDX-License-Identifier: MIT
"""Strict stored-receipt validation and deterministic drift classification."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from concierge.bounty_contract_common import (
    RECEIPT_SCHEMA,
    VERIFICATION_SCHEMA,
    _AUTHORITY,
    _DRIFT_ORDER,
    _MAINTAINER_ASSOCIATIONS,
    _MAX_COLLECTION,
    _SHA256_RE,
    BountyContractInputError,
    _hash_json,
    _timestamp,
    _validate_issue_number,
    _validate_repo,
)

def _stored_string(value: Any, field: str, *, nonempty: bool = True) -> str:
    if type(value) is not str or (nonempty and not value):
        raise BountyContractInputError(f"receipt {field} must be a string")
    return value

def _stored_sha(value: Any, field: str) -> str:
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        raise BountyContractInputError(f"receipt {field} must be lowercase SHA-256")
    return value

def _validate_stored_labels(value: Any) -> list[dict[str, Any]]:
    if type(value) is not list or len(value) > _MAX_COLLECTION:
        raise BountyContractInputError("receipt labels must be a bounded list")
    expected = {"id", "node_id", "name", "color", "default", "description_sha256"}
    result: list[dict[str, Any]] = []
    ids: set[int] = set()
    names: set[str] = set()
    for item in value:
        if type(item) is not dict or set(item) != expected:
            raise BountyContractInputError("receipt label is malformed")
        label_id = item["id"]
        if isinstance(label_id, bool) or not isinstance(label_id, int) or label_id <= 0:
            raise BountyContractInputError("receipt label id is invalid")
        name = _stored_string(item["name"], "label name")
        name_key = name.casefold()
        if label_id in ids or name_key in names:
            raise BountyContractInputError("receipt labels contain duplicates")
        ids.add(label_id)
        names.add(name_key)
        color = _stored_string(item["color"], "label color").lower()
        if not re.fullmatch(r"[0-9a-f]{6}", color):
            raise BountyContractInputError("receipt label color is invalid")
        if type(item["default"]) is not bool:
            raise BountyContractInputError("receipt label default flag is invalid")
        result.append(
            {
                "id": label_id,
                "node_id": _stored_string(item["node_id"], "label node_id"),
                "name": name,
                "color": color,
                "default": item["default"],
                "description_sha256": _stored_sha(
                    item["description_sha256"], "label description_sha256"
                ),
            }
        )
    if result != sorted(result, key=lambda item: (item["name"].casefold(), item["id"])):
        raise BountyContractInputError("receipt labels must be canonically sorted")
    return result

def _validate_stored_assignees(value: Any) -> list[dict[str, Any]]:
    if type(value) is not list or len(value) > _MAX_COLLECTION:
        raise BountyContractInputError("receipt assignees must be a bounded list")
    expected = {"id", "node_id", "login", "type"}
    result: list[dict[str, Any]] = []
    ids: set[int] = set()
    logins: set[str] = set()
    for item in value:
        if type(item) is not dict or set(item) != expected:
            raise BountyContractInputError("receipt assignee is malformed")
        user_id = item["id"]
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise BountyContractInputError("receipt assignee id is invalid")
        login = _stored_string(item["login"], "assignee login")
        if login != login.casefold():
            raise BountyContractInputError("receipt assignee login is not normalized")
        if user_id in ids or login in logins:
            raise BountyContractInputError("receipt assignees contain duplicates")
        ids.add(user_id)
        logins.add(login)
        result.append(
            {
                "id": user_id,
                "node_id": _stored_string(item["node_id"], "assignee node_id"),
                "login": login,
                "type": _stored_string(item["type"], "assignee type"),
            }
        )
    if result != sorted(result, key=lambda item: (item["login"], item["id"])):
        raise BountyContractInputError("receipt assignees must be canonically sorted")
    return result

def _validate_stored_author(value: Any) -> dict[str, str]:
    if type(value) is not dict or set(value) != {"login_sha256", "type", "association"}:
        raise BountyContractInputError("receipt contract author is malformed")
    return {
        "login_sha256": _stored_sha(value["login_sha256"], "author login_sha256"),
        "type": _stored_string(value["type"], "author type"),
        "association": _stored_string(value["association"], "author association"),
    }

def _validate_stored_milestone(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    expected = {
        "number",
        "state",
        "due_on",
        "title_sha256",
        "description_sha256",
    }
    if type(value) is not dict or set(value) != expected:
        raise BountyContractInputError("receipt milestone is malformed")
    number = value["number"]
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise BountyContractInputError("receipt milestone number is invalid")
    if value["state"] not in {"open", "closed"}:
        raise BountyContractInputError("receipt milestone state is invalid")
    due_on = value["due_on"]
    if due_on is not None:
        due_on = _timestamp(due_on, "receipt milestone due_on")
    return {
        "number": number,
        "state": value["state"],
        "due_on": due_on,
        "title_sha256": _stored_sha(value["title_sha256"], "milestone title_sha256"),
        "description_sha256": _stored_sha(
            value["description_sha256"], "milestone description_sha256"
        ),
    }

def _validate_stored_terms(value: Any) -> list[dict[str, Any]]:
    if type(value) is not list or len(value) > _MAX_COLLECTION:
        raise BountyContractInputError("receipt maintainer_terms must be a bounded list")
    expected = {
        "comment_id",
        "node_id",
        "author_login_sha256",
        "author_association",
        "created_at",
        "updated_at",
        "body_sha256",
    }
    result: list[dict[str, Any]] = []
    ids: list[int] = []
    for item in value:
        if type(item) is not dict or set(item) != expected:
            raise BountyContractInputError("receipt maintainer term is malformed")
        comment_id = item["comment_id"]
        if isinstance(comment_id, bool) or not isinstance(comment_id, int) or comment_id <= 0:
            raise BountyContractInputError("receipt maintainer comment ID is invalid")
        ids.append(comment_id)
        association = item["author_association"]
        if association not in _MAINTAINER_ASSOCIATIONS:
            raise BountyContractInputError(
                "receipt maintainer comment association is invalid"
            )
        result.append(
            {
                "comment_id": comment_id,
                "node_id": _stored_string(item["node_id"], "comment node_id"),
                "author_login_sha256": _stored_sha(
                    item["author_login_sha256"], "comment author_login_sha256"
                ),
                "author_association": association,
                "created_at": _timestamp(item["created_at"], "receipt comment created_at"),
                "updated_at": _timestamp(item["updated_at"], "receipt comment updated_at"),
                "body_sha256": _stored_sha(item["body_sha256"], "comment body_sha256"),
            }
        )
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise BountyContractInputError(
            "receipt maintainer comments must be sorted and unique"
        )
    return result

def _validate_contract(value: Any) -> dict[str, Any]:
    expected = {
        "author",
        "state",
        "state_reason",
        "locked",
        "title_sha256",
        "body_sha256",
        "labels",
        "assignees",
        "milestone",
        "maintainer_terms",
    }
    if type(value) is not dict or set(value) != expected:
        raise BountyContractInputError("receipt contract shape is invalid")
    if value["state"] not in {"open", "closed"}:
        raise BountyContractInputError("receipt issue state is invalid")
    state_reason = value["state_reason"]
    if state_reason is not None and type(state_reason) is not str:
        raise BountyContractInputError("receipt issue state_reason is invalid")
    if type(value["locked"]) is not bool:
        raise BountyContractInputError("receipt issue locked state is invalid")
    return {
        "author": _validate_stored_author(value["author"]),
        "state": value["state"],
        "state_reason": state_reason,
        "locked": value["locked"],
        "title_sha256": _stored_sha(value["title_sha256"], "title_sha256"),
        "body_sha256": _stored_sha(value["body_sha256"], "body_sha256"),
        "labels": _validate_stored_labels(value["labels"]),
        "assignees": _validate_stored_assignees(value["assignees"]),
        "milestone": _validate_stored_milestone(value["milestone"]),
        "maintainer_terms": _validate_stored_terms(value["maintainer_terms"]),
    }

def validate_receipt(value: Any) -> dict[str, Any]:
    """Strictly validate a stored receipt and its consistency digests."""
    expected = {
        "schema",
        "source",
        "captured_at",
        "source_updated_at",
        "contract",
        "contract_sha256",
        "authority",
        "receipt_sha256",
    }
    if type(value) is not dict or set(value) != expected:
        raise BountyContractInputError("receipt shape is invalid")
    if value["schema"] != RECEIPT_SCHEMA:
        raise BountyContractInputError("receipt schema is not authoritative")
    source = value["source"]
    source_expected = {
        "repo",
        "issue",
        "canonical_url",
        "api_url",
        "issue_id",
        "node_id",
        "created_at",
    }
    if type(source) is not dict or set(source) != source_expected:
        raise BountyContractInputError("receipt source shape is invalid")
    repo = _validate_repo(source["repo"])
    number = _validate_issue_number(source["issue"])
    canonical_url = f"https://github.com/{repo}/issues/{number}"
    api_url = f"https://api.github.com/repos/{repo}/issues/{number}"
    if source["canonical_url"] != canonical_url or source["api_url"] != api_url:
        raise BountyContractInputError("receipt source URLs are not canonical")
    issue_id = source["issue_id"]
    if isinstance(issue_id, bool) or not isinstance(issue_id, int) or issue_id <= 0:
        raise BountyContractInputError("receipt issue_id is invalid")
    checked_source = {
        "repo": repo,
        "issue": number,
        "canonical_url": canonical_url,
        "api_url": api_url,
        "issue_id": issue_id,
        "node_id": _stored_string(source["node_id"], "source node_id"),
        "created_at": _timestamp(source["created_at"], "receipt source created_at"),
    }
    contract = _validate_contract(value["contract"])
    contract_sha = _stored_sha(value["contract_sha256"], "contract_sha256")
    if _hash_json(contract) != contract_sha:
        raise BountyContractInputError("receipt contract digest does not match contract")
    if value["authority"] != _AUTHORITY:
        raise BountyContractInputError("receipt authority boundary is invalid")
    core = {
        "schema": RECEIPT_SCHEMA,
        "source": checked_source,
        "captured_at": _timestamp(value["captured_at"], "receipt captured_at"),
        "source_updated_at": _timestamp(
            value["source_updated_at"], "receipt source_updated_at"
        ),
        "contract": contract,
        "contract_sha256": contract_sha,
        "authority": deepcopy(_AUTHORITY),
    }
    receipt_sha = _stored_sha(value["receipt_sha256"], "receipt_sha256")
    if _hash_json(core) != receipt_sha:
        raise BountyContractInputError("receipt digest does not match receipt contents")
    return {**core, "receipt_sha256": receipt_sha}

def _reason_codes(baseline: dict[str, Any], live: dict[str, Any]) -> list[str]:
    reasons: set[str] = set()
    if baseline["source"] != live["source"]:
        reasons.add("SOURCE_IDENTITY_CHANGED")
    old = baseline["contract"]
    new = live["contract"]
    if old["author"] != new["author"]:
        reasons.add("ISSUE_AUTHORITY_CHANGED")
    if (old["state"], old["state_reason"]) != (new["state"], new["state_reason"]):
        reasons.add("ISSUE_STATE_CHANGED")
    if old["locked"] != new["locked"]:
        reasons.add("LOCK_STATE_CHANGED")
    if old["title_sha256"] != new["title_sha256"]:
        reasons.add("TITLE_CHANGED")
    if old["body_sha256"] != new["body_sha256"]:
        reasons.add("BODY_CHANGED")
    if old["labels"] != new["labels"]:
        reasons.add("LABELS_CHANGED")
    if old["assignees"] != new["assignees"]:
        reasons.add("ASSIGNEES_CHANGED")
    if old["milestone"] != new["milestone"]:
        reasons.add("MILESTONE_CHANGED")
    if old["maintainer_terms"] != new["maintainer_terms"]:
        reasons.add("MAINTAINER_TERMS_CHANGED")
    if baseline["contract_sha256"] != live["contract_sha256"] and not reasons:
        reasons.add("UNCLASSIFIED_CONTRACT_DRIFT")
    return [code for code in _DRIFT_ORDER if code in reasons]

def _verification_receipt(
    baseline: dict[str, Any],
    *,
    checked_at: str,
    disposition: str,
    reason_codes: list[str],
    live: dict[str, Any] | None,
) -> dict[str, Any]:
    core = {
        "schema": VERIFICATION_SCHEMA,
        "source": baseline["source"],
        "baseline_receipt_sha256": baseline["receipt_sha256"],
        "baseline_contract_sha256": baseline["contract_sha256"],
        "checked_at": checked_at,
        "disposition": disposition,
        "reason_codes": reason_codes,
        "live": live,
        "authority": deepcopy(_AUTHORITY),
    }
    return {**core, "verification_sha256": _hash_json(core)}
