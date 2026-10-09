# SPDX-License-Identifier: MIT
"""Fail-closed issue-level BountyHub listing retraction fence.

This is a *second* gate after bountyhub_canonical_admission.assess: a current,
OPEN GitHub issue can still have a withdrawn/test-only BountyHub posting.
Only operator-verified first-party issue-creator statements are admitted.
Evidence is a pinned manual snapshot, not a live GitHub assertion or a
claim that a sponsor defaulted on money. Existing claims remain preserved.

Run from repository root:
    python -m concierge.bountyhub_issue_retractions packet.json \
        --manifest policies/bountyhub_withdrawn_issues_v1.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

SCHEMA = "bountyhub-issue-withdrawals/v1"
ISSUE_RE = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)$")
COMMENT_RE = re.compile(
    r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)$"
)
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
RETRACTIONS = {"TEST_LISTING_WITHDRAWN", "ISSUE_CREATOR_CANCELLED"}
REASON = "ORIGINAL_LISTING_WITHDRAWN_BY_ISSUE_CREATOR"


class RetractionError(ValueError):
    """Invalid or unsafe issue withdrawal evidence."""


def _nonempty(v, name, maxlen=2000):
    if not isinstance(v, str) or not v or v != v.strip() or len(v) > maxlen:
        raise RetractionError(f"{name} must be bounded nonempty text")
    return v


def _github_url(value, name, *, comment=False):
    value = _nonempty(value, name)
    u = urlsplit(value)
    if (u.scheme != "https" or u.netloc != "github.com" or u.username or u.password
            or u.port or u.query or (bool(u.fragment) != comment)):
        raise RetractionError(f"{name} must be a canonical GitHub issue/comment link")
    m = (COMMENT_RE if comment else ISSUE_RE).fullmatch(u.path)
    if not m:
        raise RetractionError(f"{name} must point to GitHub issue, not PR or repo")
    if comment and not re.fullmatch(r"issuecomment-[1-9][0-9]*", u.fragment):
        raise RetractionError(f"{name} must identify one GitHub issue comment")
    return (m.group(1).casefold(), m.group(2).casefold(), int(m.group(3)))


def _validate_manifest(manifest):
    if type(manifest) is not dict or manifest.get("schema") != SCHEMA:
        raise RetractionError("unknown retraction manifest schema")
    records = manifest.get("records")
    if type(records) is not list or len(records) > 500:
        raise RetractionError("records must be a bounded list")
    seen = set()
    by_issue = {}
    for i, item in enumerate(records):
        tag = f"records[{i}]"
        if type(item) is not dict:
            raise RetractionError(f"{tag} must be an object")
        scope = _github_url(item.get("issue_url"), f"{tag}.issue_url")
        comment_url = _nonempty(item.get("comment_url"), f"{tag}.comment_url")
        if _github_url(comment_url, f"{tag}.comment_url", comment=True) != scope:
            raise RetractionError(f"{tag} comment is not on same issue")
        issue_creator = _nonempty(item.get("issue_creator_login"), f"{tag}.issue_creator_login", 39)
        commenter = _nonempty(item.get("comment_author_login"), f"{tag}.comment_author_login", 39)
        if not LOGIN_RE.fullmatch(issue_creator) or not LOGIN_RE.fullmatch(commenter):
            raise RetractionError(f"{tag} invalid GitHub login")
        if issue_creator.casefold() != commenter.casefold():
            raise RetractionError(f"{tag} withdrawal must be by original issue creator")
        if item.get("withdrawal_type") not in RETRACTIONS:
            raise RetractionError(f"{tag} unknown withdrawal type")
        statement = _nonempty(item.get("quoted_statement"), f"{tag}.quoted_statement", 1000)
        if len(statement) < 12:
            raise RetractionError(f"{tag} quoted source text is too short")
        if item.get("operator_verified_source") is not True:
            raise RetractionError(f"{tag} requires human/provider-verified first-party source")
        # The source attestation is supplied by the operator, not cryptographic
        # proof. Fetch the linked issue/comments before relying on new records.
        key = (scope, comment_url)
        if key in seen:
            raise RetractionError(f"{tag} duplicate first-party comment reference")
        seen.add(key)
        by_issue.setdefault(scope, []).append(item)
    return by_issue


def apply_withdrawals(canonical_decision, manifest):
    """Never upgrade canonical HOLD; only downgrade READY if a retraction matches."""
    if type(canonical_decision) is not dict:
        raise RetractionError("canonical result must be an object")
    if canonical_decision.get("decision") not in {"HOLD", "READY_FOR_NEW_BUILD"}:
        raise RetractionError("not a canonical BountyHub decision")
    if type(canonical_decision.get("reason_codes")) is not list:
        raise RetractionError("missing canonical reason codes")
    scope = _github_url(canonical_decision.get("issue_url"), "canonical.issue_url")
    by_issue = _validate_manifest(manifest)
    matches = by_issue.get(scope, [])
    result = dict(canonical_decision)
    result["withdrawal_manifest_schema"] = SCHEMA
    result["withdrawal_evidence_urls"] = sorted(x["comment_url"] for x in matches)
    result["new_work_only"] = True
    result["existing_claims_preserved"] = True
    result["evidence_requires_independent_source_readback"] = True
    if matches:
        result["decision"] = "HOLD"
        result["reason_codes"] = sorted(set(result["reason_codes"]) | {REASON})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path, help="canonical admission input packet")
    parser.add_argument("--manifest", type=Path, required=True, help="reviewed first-party withdrawals")
    parser.add_argument("--output", type=Path, help="optional JSON output path")
    args = parser.parse_args()
    from concierge.bountyhub_canonical_admission import assess
    packet = json.loads(args.packet.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = apply_withdrawals(assess(packet), manifest)
    output = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
