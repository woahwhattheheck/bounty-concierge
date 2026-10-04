# SPDX-License-Identifier: MIT
"""Recognize explicit, source-bound restrictions on automated submissions.

This consumes caller-supplied retained observations. It performs no provider
reads and does not restrict private or owner-fork work.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit


AUTOMATED_UPSTREAM_SUBMISSION = "automated_upstream_submission"
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MODEL = r"(?:LLM(?:-generated)?|AI-generated|model-generated|large language model)"
_PROHIBITIONS = (
    re.compile(
        rf"\b(?:must|shall|may)\s+not\s+submit\b[^.!?]*\b{_MODEL}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:must|shall|may)\s+not\s+use\s+(?:an?\s+)?{_MODEL}"
        r"\s+to\s+(?:author|write|generate)\b[^.!?]*\bsubmit(?:ted|sions?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:do|does|will)\s+not\s+accept\s+(?:any\s+)?{_MODEL}"
        r"\s+(?:code|tests?|content|submissions?|pull requests?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_MODEL}\s+(?:code|tests?|content|submissions?|pull requests?)"
        r"\s+(?:is|are)\s+(?:strictly\s+)?(?:prohibited|forbidden|not accepted)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:must|shall|may)\s+not\s+(?:direct or permit|direct|permit|use)"
        r"\s+(?:an?\s+)?(?:autonomous|semi-autonomous)\b[^.!?]*\bagent\b"
        r"[^.!?]*\b(?:submit|open|comment)\b[^.!?]*\b(?:issue|pull request)\b",
        re.IGNORECASE,
    ),
)
_NONWRITER_SCOPE = re.compile(
    r"\b(?:this\s+)?policy\s+applies\s+to\s+(?:every|any|all)\s+"
    r"(?:person|people|contributors?|users?)(?:\s+who)?\s+"
    r"(?:does\s+not\s+have|do\s+not\s+have|without)\s+write\s+access\b",
    re.IGNORECASE,
)
_CONDITIONAL = re.compile(
    r"\b(?:unless|except|provided|if|as long as)\b|"
    r"\bwithout\s+(?:human|maintainer|independent|review|disclosure)\b",
    re.IGNORECASE,
)


class RepositoryPolicyInputError(ValueError):
    """A supplied repository-policy observation is malformed or inconsistent."""


def _sentences(text: str) -> list[str]:
    """Ignore quoted examples and code before recognizing normative prose."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        match = re.match(r"(`{3,}|~{3,})", stripped)
        if match:
            marker = match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            lines.append("")
            continue
        if fence is not None or stripped.startswith(">"):
            continue
        if re.match(r"(?i)^(?:examples?|quoted text)\s*:", stripped):
            continue
        lines.append(re.sub(r"`+[^`]*`+", "", line))
    return [
        " ".join(part.split())
        for part in re.split(r"(?<=[.!?])\s+|\n\s*\n", "\n".join(lines))
        if part.strip()
    ]


def _document_rule(document: dict[str, Any], repo: str, revision: str) -> dict[str, Any]:
    text = document.get("text")
    source_url = document.get("source_url")
    blob_sha = document.get("git_blob_sha")
    content_sha = document.get("content_sha256")
    if not isinstance(text, str) or not isinstance(source_url, str):
        raise RepositoryPolicyInputError("repository policy text and source_url must be strings")
    if blob_sha is not None and (not isinstance(blob_sha, str) or not _SHA.fullmatch(blob_sha)):
        raise RepositoryPolicyInputError("repository policy blob_sha must be a Git blob SHA")
    if not isinstance(content_sha, str) or not _SHA256.fullmatch(content_sha):
        raise RepositoryPolicyInputError("repository policy content_sha256 is required")
    try:
        source = urlsplit(source_url)
        parts = source.path.strip("/").split("/")
        pinned = (
            source.scheme == "https" and source.netloc == "github.com"
            and not source.query and not source.fragment
            and len(parts) >= 5 and parts[2] == "blob"
            and parts[3] == revision
            and "/".join(parts[:2]).casefold() == repo.casefold()
        )
        data = text.encode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise RepositoryPolicyInputError("repository policy source is malformed") from exc
    if not pinned:
        raise RepositoryPolicyInputError("repository policy source must pin the same repository")
    if hashlib.sha256(data).hexdigest() != content_sha:
        raise RepositoryPolicyInputError("repository policy text does not match its SHA-256")
    actual_sha = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if blob_sha is not None and actual_sha != blob_sha:
        raise RepositoryPolicyInputError("repository policy text does not match its Git blob SHA")

    sentences = _sentences(text)
    prohibited = any(
        not _CONDITIONAL.search(sentence) and pattern.search(sentence)
        for sentence in sentences for pattern in _PROHIBITIONS
    )
    scoped = any(_NONWRITER_SCOPE.search(sentence) for sentence in sentences)
    return {
        "source_url": source_url, "git_blob_sha": blob_sha,
        "content_sha256": content_sha, "prohibited": prohibited,
        "scope": "contributors_without_write" if scoped else "all_contributors",
    }


def evaluate_repository_policy(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Apply the collector's retained sidecar only to its declared target/action.

    Checksums bind retained bytes, not caller authenticity. Role observations
    must come from the submitting account's read of the same target repository.
    Missing policy/method never creates a new gate. Conditional prose is left
    unclassified rather than being promoted into an unconditional prohibition.
    """
    if snapshot.get("submission_method") != AUTOMATED_UPSTREAM_SUBMISSION:
        return None
    context = snapshot.get("submission_policy_context")
    if context is None:
        return None
    if not isinstance(context, dict) or context.get("schema") != "submission-policy-context/v1":
        raise RepositoryPolicyInputError("expected submission-policy-context/v1 evidence")
    repo = context.get("submission_repo")
    revision = context.get("snapshot_sha")
    observed_at = context.get("observed_at")
    if not isinstance(repo, str) or not _REPO.fullmatch(repo):
        raise RepositoryPolicyInputError("submission policy repository must be owner/name")
    if revision is not None and (not isinstance(revision, str) or not _SHA.fullmatch(revision)):
        raise RepositoryPolicyInputError("submission policy snapshot_sha must be a Git commit SHA")
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("missing timezone")
    except (AttributeError, TypeError, ValueError) as exc:
        raise RepositoryPolicyInputError("submission policy observed_at must have a timezone") from exc
    target = snapshot.get("submission_target")
    target_repo = target.get("repository") if isinstance(target, dict) else snapshot.get("repo")
    result: dict[str, Any] = {
        "repository": repo.casefold(),
        "snapshot_sha": revision,
        "observed_at": observed_at,
        "submission_method": AUTOMATED_UPSTREAM_SUBMISSION,
        "upstream_write": None,
        "status": "NO_EXPLICIT_PROHIBITION",
        "reason_code": None,
    }
    if not isinstance(target_repo, str):
        result["status"] = "SUBMISSION_TARGET_UNKNOWN"
        return result
    if target_repo.casefold() != repo.casefold():
        result["status"] = "DIFFERENT_SUBMISSION_TARGET"
        return result
    documents = context.get("documents")
    if not isinstance(documents, list) or any(not isinstance(item, dict) for item in documents):
        raise RepositoryPolicyInputError("submission policy documents must be a list of records")
    read_documents = [item for item in documents if item.get("status") == "READ"]
    if read_documents and (not isinstance(revision, str) or not _SHA.fullmatch(revision)):
        raise RepositoryPolicyInputError("read policy documents require a pinned snapshot SHA")
    rules = [_document_rule(item, repo, revision) for item in read_documents]
    result["documents"] = rules
    prohibited = [rule for rule in rules if rule["prohibited"]]
    if not prohibited:
        return result

    role = context.get("repository_role")
    push: bool | None = None
    if isinstance(role, dict) and role.get("status") == "READ":
        expected_url = f"https://api.github.com/repos/{repo}".casefold()
        role_url = role.get("source_url")
        permissions = role.get("permissions")
        if isinstance(role_url, str) and role_url.casefold() == expected_url:
            if isinstance(permissions, dict) and type(permissions.get("push")) is bool:
                push = permissions["push"]
    result["upstream_write"] = push
    if any(rule["scope"] == "all_contributors" for rule in prohibited) or push is False:
        result["status"] = "PROHIBITED_FOR_METHOD"
        result["reason_code"] = "AUTOMATED_UPSTREAM_SUBMISSION_PROHIBITED"
    elif push is None:
        result["status"] = "UPSTREAM_ROLE_UNKNOWN"
        result["reason_code"] = "AUTOMATED_SUBMISSION_UPSTREAM_ROLE_UNKNOWN"
    else:
        result["status"] = "WRITE_ROLE_EXEMPT"
    return result
