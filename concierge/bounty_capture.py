# SPDX-License-Identifier: MIT
"""Controlled preflight captures and deterministic, offline gate replay.

Captures retain issue and maintainer text for recalculation. They belong in the
trusted local custody of the operator; routed evidence deliberately omits that
text. The unkeyed consistency digest is neither a provider signature nor proof
of an authenticated principal or observation time. Replay verifies the supplied
evidence and preserves the live gate sequence; it does not authenticate GitHub.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit

from concierge.submission_packet import SubmissionPacketInputError, validate_submission_target


CAPTURE_SCHEMA = "bounty-preflight-capture/v1"
_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_PRINCIPAL_CHECKS = frozenset(
    {"NO_ASSIGNEES", "AUTHENTICATED_SAME_TOKEN", "ANONYMOUS"}
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_AUDIT_FIELDS = frozenset(
    {"issue_state", "open_pr_count", "stale_listing_signal", "search_truncated"}
)
_CAPTURE_FIELDS = frozenset(
    {
        "schema", "repo", "number", "issue_url", "policy", "observation",
        "baseline", "authority", "assignment", "generation", "checks",
        "qualification", "receipt_sha256",
    }
)


class CaptureInputError(ValueError):
    """Capture evidence is incomplete, inconsistent, or not supported."""


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise CaptureInputError("capture must contain finite JSON values") from exc


def capture_digest(value: Any) -> str:
    """Hash canonical JSON for consistency, without asserting provenance."""
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _json_copy(value: Any) -> Any:
    return json.loads(_json_bytes(value))


def _object(value: Any, fields: set[str] | frozenset[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise CaptureInputError(f"{name} has an invalid object schema")
    return value


def _submission_target(value: Any, issue_url: str) -> dict[str, str]:
    try:
        return validate_submission_target(value, issue_url)
    except SubmissionPacketInputError as exc:
        raise CaptureInputError(str(exc)) from exc


def _string(value: Any, name: str, *, empty: bool = False) -> str:
    if type(value) is not str or (not empty and not value):
        raise CaptureInputError(f"{name} must be a string")
    return value


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise CaptureInputError(f"{name} must be an integer in its allowed range")
    return value


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise CaptureInputError(f"{name} must be boolean")
    return value


def _digest(value: Any, name: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise CaptureInputError(f"{name} must be a canonical SHA256 digest")
    return value


def _timestamp(value: Any, name: str) -> datetime:
    _string(value, name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError) as exc:
        raise CaptureInputError(f"{name} must be an aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CaptureInputError(f"{name} must be an aware ISO timestamp")
    return parsed.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _time_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _source_url(value: Any) -> str:
    """Allow the requested GitHub endpoint only, never query/header material."""
    _string(value, "source URL")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https" and parsed.netloc == "api.github.com"
            and parsed.username is None and parsed.password is None
            and not parsed.query and not parsed.fragment
            and parsed.path.startswith("/")
            and not any(character.isspace() for character in value)
        )
    except ValueError as exc:
        raise CaptureInputError("source URL is not a canonical GitHub endpoint") from exc
    if not valid:
        raise CaptureInputError("source URL is not a canonical GitHub endpoint")
    return value


class _CaptureResponse:
    """Observe successful status validation and the first successful decode."""

    def __init__(self, owner: CaptureSession, response: Any) -> None:
        self._owner = owner
        self._response = response
        self._status_checked = False
        self._decoded_at: datetime | None = None
        self._complete = False

    def raise_for_status(self) -> Any:
        try:
            result = self._response.raise_for_status()
        except BaseException:
            self._owner._failed = True
            raise
        self._status_checked = True
        self._finish()
        return result

    def json(self, *args: Any, **kwargs: Any) -> Any:
        try:
            result = self._response.json(*args, **kwargs)
        except BaseException:
            self._owner._failed = True
            raise
        if self._decoded_at is None:
            self._decoded_at = _now()
        self._finish()
        return result

    def _finish(self) -> None:
        if self._complete or not self._status_checked or self._decoded_at is None:
            return
        self._owner._complete_read(self._decoded_at)
        self._complete = True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)


class CaptureSession:
    """Observe borrowed-session GETs without changing its ownership/lifetime.

    Install beneath the captured-issue adapter so in-memory issue replay is not
    counted as another provider read. Successful observation seals this wrapper;
    neither observation nor repeated JSON decoding can refresh its clock.
    """

    def __init__(self, session: Any) -> None:
        if not callable(getattr(session, "get", None)):
            raise CaptureInputError("capture session requires a GET-capable session")
        self._session = session
        self._started_at: datetime | None = None
        self._completed_at: datetime | None = None
        self._request_count = 0
        self._source_urls: set[str] = set()
        self._pending = 0
        self._failed = False
        self._sealed = False

    def get(self, url: str, **kwargs: Any) -> _CaptureResponse:
        if self._sealed:
            raise CaptureInputError("capture observation is already sealed")
        try:
            source_url = _source_url(url)
        except CaptureInputError:
            self._failed = True
            raise
        if self._started_at is None:
            self._started_at = _now()
        self._request_count += 1
        self._source_urls.add(source_url)
        self._pending += 1
        try:
            response = self._session.get(url, **kwargs)
        except BaseException:
            self._failed = True
            raise
        return _CaptureResponse(self, response)

    def _complete_read(self, completed_at: datetime) -> None:
        if (
            self._started_at is None or completed_at < self._started_at
            or (self._completed_at is not None and completed_at < self._completed_at)
        ):
            self._failed = True
            raise CaptureInputError("capture observation clock moved backwards")
        self._completed_at = completed_at
        self._pending -= 1

    def observation(self) -> dict[str, Any]:
        if (
            self._failed or self._pending or self._request_count < 1
            or self._started_at is None or self._completed_at is None
        ):
            raise CaptureInputError("capture has unfinished, failed, or missing provider reads")
        self._sealed = True
        return {
            "started_at": _time_text(self._started_at),
            "completed_at": _time_text(self._completed_at),
            "request_count": self._request_count,
            "source_urls": sorted(self._source_urls),
        }


def _identity(repo: Any, number: Any) -> tuple[str, int, str]:
    _string(repo, "repo")
    normalized = repo.strip().casefold()
    if _REPO.fullmatch(normalized) is None or any(
        part in {".", ".."} for part in normalized.split("/")
    ):
        raise CaptureInputError("repo must be an owner/repository slug")
    issue_number = _integer(number, "number", minimum=1)
    return normalized, issue_number, f"https://github.com/{normalized}/issues/{issue_number}"


def _audit(value: Any, name: str) -> dict[str, Any]:
    from concierge.bounty_preflight import BountyPreflightError, _audit_dispatch_marker

    result = _object(value, _AUDIT_FIELDS, name)
    try:
        _audit_dispatch_marker(result)
    except BountyPreflightError as exc:
        raise CaptureInputError(f"{name} contains invalid canonical audit fields") from exc
    return result


def _reduce_audit(value: Any) -> dict[str, Any]:
    if type(value) is not dict or not _AUDIT_FIELDS.issubset(value):
        raise CaptureInputError("canonical audit is missing required fields")
    return dict(_audit({key: value[key] for key in sorted(_AUDIT_FIELDS)}, "canonical audit"))


def _markers(value: Any, name: str) -> list[list[Any]]:
    if type(value) is not list:
        raise CaptureInputError(f"{name} must contain complete comment generation markers")
    seen: set[int] = set()
    for marker in value:
        if type(marker) is not list or len(marker) != 3:
            raise CaptureInputError(f"{name} contains an invalid marker")
        identifier = _integer(marker[0], "comment id", minimum=1)
        _timestamp(marker[1], "comment updated_at")
        _digest(marker[2], "comment generation digest")
        if identifier in seen:
            raise CaptureInputError(f"{name} contains duplicate comment identities")
        seen.add(identifier)
    return value


def _issue_projection(capture: dict[str, Any]) -> dict[str, Any]:
    baseline = capture["baseline"]
    return {
        "repo": capture["repo"], "number": capture["number"],
        "title": baseline["title"], "body": baseline["body"],
        "labels": baseline["labels"],
        "issue_state": baseline["canonical_audit"]["issue_state"],
        "author_association": capture["authority"]["issue_author_association"],
        "updated_at": capture["generation"]["issue_updated_at"],
        "comment_count": capture["generation"]["issue_comment_count"],
    }


def _comment_binding(comment: dict[str, Any]) -> str:
    return capture_digest(
        {key: comment[key] for key in ("id", "updated_at", "body", "author_association")}
    )


def _validate_observation(
    value: Any, repo: str, number: int, principal: str,
    *, submission_target: dict[str, str] | None = None,
) -> None:
    observation = _object(
        value, {"started_at", "completed_at", "request_count", "source_urls"}, "observation"
    )
    started = _timestamp(observation["started_at"], "observation.started_at")
    completed = _timestamp(observation["completed_at"], "observation.completed_at")
    # Replay is deterministic. The supply router compares both endpoints with
    # its explicit evaluation time; only the online session creates read times.
    if started > completed:
        raise CaptureInputError("observation interval is unordered")
    count = _integer(observation["request_count"], "request_count", minimum=1)
    urls = observation["source_urls"]
    if type(urls) is not list or not urls or any(type(url) is not str for url in urls):
        raise CaptureInputError("source_urls must be a non-empty list of endpoint strings")
    if urls != sorted(set(urls)) or count < len(urls):
        raise CaptureInputError("source URL set and request count are inconsistent")
    issue_path = f"/repos/{repo}/issues/{number}"
    allowed = {issue_path, f"{issue_path}/comments", "/search/issues", "/user"}
    seen_paths: set[str] = set()
    pull_repos = {repo}
    if submission_target is not None:
        pull_repos.add(submission_target["repository"].casefold())
    pull_pattern = re.compile(r"/repos/([^/]+/[^/]+)/pulls/[1-9][0-9]*\Z")
    for url in urls:
        path = urlsplit(_source_url(url)).path.casefold()
        pull = pull_pattern.fullmatch(path)
        if path not in allowed and (pull is None or pull[1] not in pull_repos):
            raise CaptureInputError(
                "source endpoint is not bound to the captured issue or submission target repository"
            )
        seen_paths.add(path)
    required = {issue_path, f"{issue_path}/comments", "/search/issues"}
    if not required.issubset(seen_paths):
        raise CaptureInputError("capture omits required issue, comment, or audit source reads")
    if ("/user" in seen_paths) != (principal == "AUTHENTICATED_SAME_TOKEN"):
        raise CaptureInputError("authenticated read basis and source reads disagree")


def _validate_capture(capture: Any, saturation_threshold: int) -> dict[str, Any]:
    fields = _CAPTURE_FIELDS
    if type(capture) is dict and "submission_target" in capture:
        fields = fields | {"submission_target"}
    value = _object(capture, fields, "capture")
    if value["schema"] != CAPTURE_SCHEMA:
        raise CaptureInputError("unsupported capture schema")
    repo, number, issue_url = _identity(value["repo"], value["number"])
    if value["repo"] != repo or value["issue_url"] != issue_url:
        raise CaptureInputError("capture issue identity is not canonical")
    submission_target = (
        _submission_target(value["submission_target"], issue_url)
        if "submission_target" in value else None
    )
    policy = _object(value["policy"], {"max_pages", "saturation_threshold"}, "policy")
    _integer(policy["max_pages"], "max_pages", minimum=1)
    _integer(policy["saturation_threshold"], "captured saturation_threshold", minimum=1)
    _integer(saturation_threshold, "saturation_threshold", minimum=1)
    if policy["saturation_threshold"] != saturation_threshold:
        raise CaptureInputError("capture and replay saturation policies differ")
    _digest(value["receipt_sha256"], "receipt_sha256")
    body = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if capture_digest(body) != value["receipt_sha256"]:
        raise CaptureInputError("capture consistency digest does not match")

    baseline = _object(
        value["baseline"],
        {"title", "body", "labels", "attempt_count", "canonical_audit"}, "baseline",
    )
    _string(baseline["title"], "title", empty=True)
    _string(baseline["body"], "body", empty=True)
    labels = baseline["labels"]
    if type(labels) is not list or any(type(label) is not str for label in labels):
        raise CaptureInputError("baseline labels must be normalized strings")
    if labels != sorted(labels):
        raise CaptureInputError("baseline labels must use canonical ordering")
    _integer(baseline["attempt_count"], "attempt_count")
    initial_audit = _audit(baseline["canonical_audit"], "baseline.canonical_audit")

    assignment = _object(
        value["assignment"],
        {"formal_assignee_count", "assigned_to_operator", "foreign_assignee_count", "principal_check"},
        "assignment",
    )
    formal = _integer(assignment["formal_assignee_count"], "formal_assignee_count")
    foreign = _integer(assignment["foreign_assignee_count"], "foreign_assignee_count")
    assigned = _boolean(assignment["assigned_to_operator"], "assigned_to_operator")
    principal = assignment["principal_check"]
    if type(principal) is not str or principal not in _PRINCIPAL_CHECKS:
        raise CaptureInputError("principal_check is not a supported authenticated-read basis")
    if formal != foreign + int(assigned):
        raise CaptureInputError("assignment counts are inconsistent")
    if (principal == "NO_ASSIGNEES") != (formal == 0):
        raise CaptureInputError("assignment count and principal check disagree")
    if assigned and principal != "AUTHENTICATED_SAME_TOKEN":
        raise CaptureInputError("operator assignment lacks same-token authenticated read evidence")
    _validate_observation(
        value["observation"], repo, number, principal, submission_target=submission_target
    )

    generation = _object(
        value["generation"],
        {
            "issue_sha256", "issue_projection_sha256", "issue_updated_at",
            "issue_comment_count", "comments", "comments_truncated", "attempt_signal_count",
        }, "generation",
    )
    _digest(generation["issue_sha256"], "initial issue generation digest")
    _digest(generation["issue_projection_sha256"], "issue projection digest")
    _timestamp(generation["issue_updated_at"], "issue_updated_at")
    comment_count = _integer(generation["issue_comment_count"], "issue_comment_count")
    markers = _markers(generation["comments"], "initial comments")
    truncated = _boolean(generation["comments_truncated"], "comments_truncated")
    signal_count = _integer(generation["attempt_signal_count"], "attempt_signal_count")
    if not baseline["attempt_count"] <= signal_count <= len(markers):
        raise CaptureInputError("attempt reduction and comment evidence counts disagree")
    if not truncated and len(markers) != comment_count:
        raise CaptureInputError("complete comment evidence does not match the issue comment count")
    if truncated and not initial_audit["search_truncated"]:
        raise CaptureInputError("comment truncation was dropped from the canonical audit")

    authority = _object(
        value["authority"], {"issue_author_association", "maintainer_comments"}, "authority"
    )
    association = _string(authority["issue_author_association"], "issue association", empty=True)
    if association != association.upper():
        raise CaptureInputError("issue association must preserve normalized live authority")
    if capture_digest(_issue_projection(value)) != generation["issue_projection_sha256"]:
        raise CaptureInputError("retained issue projection does not match its content binding")
    comments = authority["maintainer_comments"]
    if type(comments) is not list:
        raise CaptureInputError("maintainer comments must be a list")
    indexed = {marker[0]: marker for marker in markers}
    seen: set[int] = set()
    for comment in comments:
        _object(
            comment,
            {"id", "updated_at", "body", "author_association", "generation_digest", "content_sha256"},
            "maintainer comment",
        )
        identifier = _integer(comment["id"], "maintainer comment id", minimum=1)
        _string(comment["body"], "maintainer comment body", empty=True)
        comment_association = _string(comment["author_association"], "maintainer association")
        if comment_association.strip().upper() not in _ASSOCIATIONS:
            raise CaptureInputError("retained comment lacks acceptance-authority association")
        _timestamp(comment["updated_at"], "maintainer comment updated_at")
        _digest(comment["generation_digest"], "maintainer comment generation digest")
        _digest(comment["content_sha256"], "maintainer comment content digest")
        if identifier in seen or indexed.get(identifier) != [
            identifier, comment["updated_at"], comment["generation_digest"]
        ]:
            raise CaptureInputError("maintainer text is not bound to its initial comment generation")
        if _comment_binding(comment) != comment["content_sha256"]:
            raise CaptureInputError("maintainer text content binding does not match")
        seen.add(identifier)

    checks = _object(value["checks"], {"audit_before", "generation", "audit_after"}, "checks")
    for key in ("audit_before", "audit_after"):
        if checks[key] is not None:
            _audit(checks[key], key)
    if checks["generation"] is not None:
        check = _object(
            checks["generation"],
            {"before_issue", "comments", "comments_truncated", "after_issue"}, "generation check",
        )
        _digest(check["before_issue"], "before issue digest")
        if check["comments"] is not None:
            _markers(check["comments"], "revalidated comments")
        if check["comments_truncated"] is not None:
            _boolean(check["comments_truncated"], "revalidated comments_truncated")
        if check["after_issue"] is not None:
            _digest(check["after_issue"], "after issue digest")
    if type(value["qualification"]) is not dict:
        raise CaptureInputError("stored qualification must be an object")
    return value


def _generation_stable(initial: dict[str, Any], check: dict[str, Any]) -> bool:
    if check["before_issue"] != initial["issue_sha256"]:
        if any(check[key] is not None for key in ("comments", "comments_truncated", "after_issue")):
            raise CaptureInputError("generation reads continued after the issue changed")
        return False
    if check["comments"] is None or check["comments_truncated"] is None:
        raise CaptureInputError("required comment generation read was not executed")
    if check["comments_truncated"] or check["comments"] != initial["comments"]:
        if check["after_issue"] is not None:
            raise CaptureInputError("generation reads continued after comments changed")
        return False
    if check["after_issue"] is None:
        raise CaptureInputError("required final issue generation read was not executed")
    return check["after_issue"] == initial["issue_sha256"]


def replay_capture(
    capture: dict[str, Any], *, saturation_threshold: int
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Recompute supplied evidence; never import a stored dispatch decision."""
    from concierge.bounty_preflight import (
        _apply_assignee_gate, _apply_audit_generation_gate, _apply_generation_gate,
        _apply_maintainer_contribution_pause_gate, _audit_dispatch_marker,
        _has_maintainer_authority, _signals_maintainer_contribution_pause,
    )
    from concierge.bounty_qualification import QualificationInputError, qualify_dispatch
    from concierge.credential_safety import apply_credential_gate, credential_gate_signal_types

    value = _validate_capture(capture, saturation_threshold)
    baseline = value["baseline"]
    try:
        qualification = qualify_dispatch(baseline, saturation_threshold=saturation_threshold)
    except QualificationInputError as exc:
        raise CaptureInputError("captured baseline cannot be qualified") from exc

    authority = value["authority"]
    texts: list[str] = []
    if _has_maintainer_authority({"author_association": authority["issue_author_association"]}):
        texts.append(baseline["body"])
    # Do not strip here: live preflight authority intentionally uses upper()
    # only. Supply acceptance independently accepts strip().upper().
    texts.extend(
        comment["body"] for comment in authority["maintainer_comments"]
        if _has_maintainer_authority(comment) and comment["body"].strip()
    )
    qualification = apply_credential_gate(qualification, credential_gate_signal_types(texts))
    qualification = _apply_maintainer_contribution_pause_gate(
        qualification, sum(_signals_maintainer_contribution_pause(text) for text in texts)
    )
    assignees = {key: value["assignment"][key] for key in (
        "formal_assignee_count", "assigned_to_operator", "foreign_assignee_count"
    )}
    qualification = _apply_assignee_gate(qualification, assignees)

    checks = value["checks"]
    statuses: dict[str, str] = {}
    initial_audit = _audit_dispatch_marker(baseline["canonical_audit"])
    for name in ("audit_before", "generation", "audit_after"):
        check = checks[name]
        if qualification["dispatch"] is not True:
            if check is not None:
                raise CaptureInputError("capture contains a check the live pipeline would skip")
            statuses[name] = "NOT_REQUIRED"
            continue
        if check is None:
            raise CaptureInputError("capture omitted a required final preflight check")
        if name == "generation":
            stable = _generation_stable(value["generation"], check)
            qualification = _apply_generation_gate(qualification, stable)
        else:
            stable = _audit_dispatch_marker(check) == initial_audit
            if name == "audit_after":
                stable = stable and checks["audit_before"] is not None and (
                    _audit_dispatch_marker(check) == _audit_dispatch_marker(checks["audit_before"])
                )
            qualification = _apply_audit_generation_gate(qualification, stable)
        statuses[name] = "PASSED" if stable else "FAILED"

    # Canonical JSON equality also distinguishes bool/int and int/float values.
    if _json_bytes(qualification) != _json_bytes(value["qualification"]):
        raise CaptureInputError("stored final qualification does not match gate replay")
    routing_snapshot = {
        "repo": value["repo"], "number": value["number"],
        "observed_at": value["observation"]["started_at"],
        **deepcopy(baseline),
        "comments": [
            {"body": comment["body"], "author_association": comment["author_association"]}
            for comment in authority["maintainer_comments"]
        ],
    }
    if "submission_target" in value:
        routing_snapshot["submission_target"] = deepcopy(value["submission_target"])
    semantic = {
        key: item for key, item in value.items()
        if key not in {"observation", "receipt_sha256"}
    }
    safe_evidence = {
        "capture_schema": CAPTURE_SCHEMA,
        "capture_receipt_sha256": value["receipt_sha256"],
        "source_generation_sha256": capture_digest(semantic),
        "observed_at": value["observation"]["started_at"],
        "completed_at": value["observation"]["completed_at"],
        "request_count": value["observation"]["request_count"],
        "source_urls": list(value["observation"]["source_urls"]),
        "check_statuses": statuses,
    }
    return routing_snapshot, qualification, safe_evidence


def make_capture(
    *, repo: str, number: int, context: dict[str, Any], issue_snapshot: dict[str, Any],
    audit: dict[str, Any], checks: dict[str, Any], qualification: dict[str, Any],
    observation: dict[str, Any], max_pages: int, saturation_threshold: int,
    submission_target: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Seal a completed online preflight; this function never mints read times."""
    from concierge.bounty_preflight import (
        BountyPreflightError, _issue_author_association, _issue_generation_marker, _label_names,
    )

    canonical_repo, issue_number, issue_url = _identity(repo, number)
    if submission_target is not None:
        submission_target = _submission_target(submission_target, issue_url)
    if type(context) is not dict or type(issue_snapshot) is not dict:
        raise CaptureInputError("capture requires completed issue context and snapshot")
    if type(audit) is not dict:
        raise CaptureInputError("capture requires a canonical audit")
    if "repo" in audit and _identity(audit["repo"], audit.get("number"))[:2] != (
        canonical_repo, issue_number
    ):
        raise CaptureInputError("canonical audit belongs to another issue")
    try:
        issue_marker = _issue_generation_marker(issue_snapshot)
        association = _issue_author_association(issue_snapshot)
        labels = list(_label_names(issue_snapshot))
        if _integer(issue_snapshot.get("number"), "snapshot issue number", minimum=1) != issue_number:
            raise CaptureInputError("captured provider issue number differs from the requested issue")
        snapshot_url = _string(issue_snapshot.get("html_url"), "snapshot issue URL")
        try:
            parsed_url = urlsplit(snapshot_url)
            url_matches = (
                parsed_url.scheme == "https" and parsed_url.netloc == "github.com"
                and not parsed_url.query and not parsed_url.fragment
                and parsed_url.path.casefold() == f"/{canonical_repo}/issues/{issue_number}"
            )
        except ValueError as exc:
            raise CaptureInputError("captured provider issue URL is malformed") from exc
        if not url_matches:
            raise CaptureInputError("captured provider issue URL differs from the requested issue")
        if _integer(context["formal_assignee_count"], "formal_assignee_count") != len(issue_marker[5]):
            raise CaptureInputError("formal assignee count differs from the captured issue generation")
        if (
            context["title"] != (issue_snapshot.get("title") or "")
            or context["body"] != (issue_snapshot.get("body") or "")
            or list(_label_names(context)) != labels
            or audit["issue_state"] != issue_snapshot["state"]
        ):
            raise CaptureInputError("baseline is not bound to its captured issue snapshot")
        raw_authority = _object(
            context["_capture_authority"],
            {"issue_author_association", "maintainer_comments"}, "captured authority",
        )
        if raw_authority["issue_author_association"] != association:
            raise CaptureInputError("issue authority differs from the captured generation")
        if type(raw_authority["maintainer_comments"]) is not list:
            raise CaptureInputError("captured maintainer comments must be a list")
        comments: list[dict[str, Any]] = []
        for raw in raw_authority["maintainer_comments"]:
            _object(
                raw, {"id", "updated_at", "body", "author_association", "generation_digest"},
                "captured maintainer comment",
            )
            comment = dict(raw)
            comment["content_sha256"] = _comment_binding(comment)
            comments.append(comment)
        result = {
            "schema": CAPTURE_SCHEMA, "repo": canonical_repo, "number": issue_number,
            "issue_url": issue_url,
            "policy": {"max_pages": max_pages, "saturation_threshold": saturation_threshold},
            "observation": observation,
            "baseline": {
                "title": context["title"], "body": context["body"], "labels": labels,
                "attempt_count": context["attempt_count"], "canonical_audit": _reduce_audit(audit),
            },
            "authority": {"issue_author_association": association, "maintainer_comments": comments},
            "assignment": {
                "formal_assignee_count": context["formal_assignee_count"],
                "assigned_to_operator": context["assigned_to_operator"],
                "foreign_assignee_count": context["foreign_assignee_count"],
                "principal_check": context["_principal_check"],
            },
            "generation": {
                "issue_sha256": capture_digest(issue_marker),
                "issue_updated_at": issue_snapshot["updated_at"],
                "issue_comment_count": issue_snapshot["comments"],
                "comments": context["_comment_generation"],
                "comments_truncated": context["comments_truncated"],
                "attempt_signal_count": context["attempt_signal_count"],
            },
            "checks": checks, "qualification": qualification,
        }
    except (KeyError, BountyPreflightError) as exc:
        raise CaptureInputError("preflight capture evidence is incomplete or malformed") from exc
    if submission_target is not None:
        result["submission_target"] = submission_target
    result["generation"]["issue_projection_sha256"] = capture_digest(_issue_projection(result))
    result = _json_copy(result)
    result["receipt_sha256"] = capture_digest(result)
    replay_capture(result, saturation_threshold=saturation_threshold)
    return result
