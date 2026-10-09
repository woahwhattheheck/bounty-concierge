# SPDX-License-Identifier: MIT
"""Source-bound test/research bounty exclusion for NEW paid-work admission.

An explicit repository owner's disclaimer is a reason to stop new unpaid work,
not evidence that anyone has already defaulted on an earned bounty. This module
never declares a repository eligible to receive work; payer proof is separate.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

_POLICY = Path(__file__).resolve().parents[1] / "policies" / "self_disclosed_test_targets_v1.json"
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
# Strong positive admissions, not vague occurrences of 'test' or 'research'.
_EXPLICIT = (
    re.compile(r"\btest[\s-]+target\s+repository\b.{0,100}\bnot\s+a\s+real\s+project\b", re.I),
    re.compile(r"\b(?:fake|mock|simulated)\s+bount(?:y|ies)\b.{0,100}\b(?:test\s+only|no\s+real\s+payments?|not\s+payable)\b", re.I),
    re.compile(r"\b(?:research|experiment)(?:al)?\s+(?:repo|project|study)\b.{0,100}\b(?:no\s+compensation|not\s+paid|fake\s+bounties)\b", re.I),
)


def _repo(value: str) -> str:
    if not isinstance(value, str) or not _REPO.fullmatch(value):
        raise ValueError("repo must be GitHub owner/name")
    owner, name = value.split("/", 1)
    if owner in (".", "..") or name in (".", ".."):
        raise ValueError("repo must not contain dot path segments")
    return value.casefold()


def _canonical_readme_url(repo: str, source_url: str) -> bool:
    if not isinstance(source_url, str):
        return False
    u = urlsplit(source_url)
    parts = u.path.strip("/").split("/")
    return (
        u.scheme == "https" and u.netloc == "github.com"
        and not u.query and not u.fragment and len(parts) >= 5
        and "/".join(parts[:2]).casefold() == repo
        and parts[2] == "blob" and bool(parts[3])
        and "/".join(parts[4:]).casefold() == "readme.md"
    )


def _visible_prose(readme: str) -> str:
    """Drop fenced samples, HTML comments and blockquotes before classification."""
    text = re.sub(r"<!--.*?-->", "", readme, flags=re.S)
    out: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        match = re.match(r"(\x60{3,}|~{3,})", stripped)
        if match:
            mark = match.group(1)[0]
            fence = mark if fence is None else (None if fence == mark else fence)
            continue
        if fence or stripped.startswith(">"):
            continue
        out.append(re.sub(r"[*_\x60#]", "", line))
    return "\n".join(out)


def is_explicit_test_disclosure(readme: str) -> bool:
    if not isinstance(readme, str):
        raise ValueError("readme text must be UTF-8 string")
    if len(readme.encode("utf-8")) > 512_000:
        raise ValueError("readme too large for source-bound screening")
    return any(p.search(line) for line in _visible_prose(readme).splitlines() for p in _EXPLICIT)


def _known_targets(policy_path: Path) -> dict[str, dict[str, str]]:
    doc = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema") != "self-disclosed-test-targets/v1":
        raise ValueError("test-target policy schema mismatch")
    rows = doc.get("blocked")
    if not isinstance(rows, list):
        raise ValueError("blocked must be an array")
    seen: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict) or not all(
            isinstance(row.get(k), str) for k in ("repo", "evidence_url", "readme_blob_sha", "reason")
        ):
            raise ValueError("invalid test-target record")
        repo = _repo(row["repo"])
        if repo in seen or not _canonical_readme_url(repo, row["evidence_url"]) or not _SHA.fullmatch(row["readme_blob_sha"]):
            raise ValueError("duplicate or noncanonical test-target evidence")
        seen[repo] = row
    return seen


def screen_paid_target(
    repo: str, *, readme: str | None = None, source_url: str | None = None,
    blob_sha: str | None = None, policy_path: Path | None = None,
) -> dict[str, Any]:
    """Block only a proven self-disclosed fake target, never assert positive payability.

    The built-in retained GitHub blob record is a manual review/maintenance
    hold. A new readme is accepted only with same-repo URL and byte-exact blob.
    The caller is responsible for fetching that blob from the actual provider.
    """
    normalized = _repo(repo)
    known = _known_targets(policy_path or _POLICY).get(normalized)
    evidence = None
    if known is not None:
        evidence = {"url": known["evidence_url"], "git_blob_sha": known["readme_blob_sha"], "kind": "retained_owner_disclosure"}
    if readme is not None:
        if not _canonical_readme_url(normalized, source_url or "") or not isinstance(blob_sha, str) or not _SHA.fullmatch(blob_sha):
            raise ValueError("readme source must pin GitHub repository and blob SHA")
        raw = readme.encode("utf-8")
        actual_blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if actual_blob != blob_sha:
            raise ValueError("readme and Git blob SHA disagree")
        if is_explicit_test_disclosure(readme):
            evidence = {"url": source_url, "git_blob_sha": blob_sha, "kind": "verified_owner_disclosure"}
    blocked = evidence is not None
    return {
        "repo": normalized,
        "disposition": "BLOCK_SELF_DISCLOSED_NONPAYABLE_TARGET" if blocked else "NO_EXPLICIT_TEST_DISCLOSURE",
        "new_paid_work_allowed": False if blocked else None,
        "reason_code": "SPONSOR_SELF_DISCLOSED_TEST_ONLY" if blocked else None,
        "evidence": evidence,
        "scope": "new_paid_work_intake_only",
        "payer_proof_derived": False,
        "existing_claims_preserved": True,
    }
