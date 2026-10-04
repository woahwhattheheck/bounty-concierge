# SPDX-License-Identifier: MIT
"""Optional, source-only context for the repository receiving a submission."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

if __package__:
    from .secure_output import SecureOutputError, create_exclusive_regular, open_verified_parent
else:
    from secure_output import SecureOutputError, create_exclusive_regular, open_verified_parent

POLICY_PATHS = (
    "CONTRIBUTING.md", ".github/CONTRIBUTING.md",
    "LLM_USAGE_POLICY.md", ".github/LLM_USAGE_POLICY.md",
)
MAX_DOCUMENT_BYTES = 256 * 1024
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+")
_SHA = re.compile(r"[0-9a-fA-F]{40}")


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_submission_repo(value):
    if not isinstance(value, str) or not _REPO.fullmatch(value) or value.split("/")[1] in {".", ".."}:
        raise ValueError("submission_repo must be an explicit owner/repository")
    return value


class RetainedSession:
    """Replay retained provider/connector readbacks through the normal adapter.

    Records contain exact requested URL/params, observed status, payload and any
    retained headers. A connector may omit the numeric HTTP status; READ then
    records successful content retrieval without inventing an HTTP code.
    """
    def __init__(self, document):
        self.records = list(document["records"])
        self.observed_at = document["observed_at"]
        self.calls = 0

    def get(self, url, *, params=None, **kwargs):
        for index, record in enumerate(self.records):
            if record["url"] == url and record.get("params", {}) == (params or {}):
                self.calls += 1
                self.records.pop(index)
                return _RetainedResponse(record)
        raise ValueError("requested source is absent from retained readbacks")


class _RetainedResponse:
    def __init__(self, record):
        self.status_code = record.get("http_status")
        self.headers = record.get("headers", {})
        self.retained_status = record["status"]
        self.payload = record.get("payload")

    def json(self):
        return self.payload

    def close(self):
        pass


def _read(session, url, headers, params=None):
    try:
        response = session.get(url, headers=headers, params=params, timeout=15,
                               allow_redirects=False)
    except (OSError, ValueError) as exc:
        # requests transport exceptions inherit OSError. Do not retain their
        # messages: those can contain credentials, proxy URLs or request data.
        missing = isinstance(session, RetainedSession) and isinstance(exc, ValueError)
        return {"status": "NOT_READ" if missing else "UNAVAILABLE",
                "http_status": None, "payload": None, "stop": True,
                "error": "RETAINED_READBACK_MISSING" if missing else "TRANSPORT_ERROR"}
    try:
        status = response.status_code
        retained = getattr(response, "retained_status", None)
        try:
            payload = response.json()
        except (TypeError, ValueError):
            payload = None
        response_headers = {str(k).casefold(): v for k, v in response.headers.items()}
        remaining = response_headers.get("x-ratelimit-remaining")
        retry_after = response_headers.get("retry-after")
        message = payload.get("message", "") if isinstance(payload, dict) else ""
        limited = status == 429 or (status == 403 and (
            str(remaining).strip() == "0" or retry_after is not None
            or (isinstance(message, str) and "rate limit" in message.casefold())))
        state = (retained if status is None and retained in {
                    "READ", "NOT_FOUND", "DENIED", "NOT_READ", "UNAVAILABLE", "RATE_LIMITED"}
                 else "READ" if status == 200
                 else "NOT_FOUND" if status == 404
                 else "DENIED" if status in (401, 403)
                 else "UNAVAILABLE")
        if limited:
            state = "RATE_LIMITED"
        return {"status": state, "http_status": status, "payload": payload,
                "stop": state in {"NOT_READ", "RATE_LIMITED"} or str(remaining).strip() == "0"}
    finally:
        response.close()


def collect_submission_policy_context(submission_repo, *, session, token=None):
    """Read one target at one pinned ref; never decide eligibility or dispatch.

    The caller owns the Session and may reuse it with preflight/batch work.
    Invoke once per target per caller batch and reuse the returned observation.
    """
    repo = validate_submission_repo(submission_repo)
    base = "https://api.github.com/repos/" + repo
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    retained = isinstance(session, RetainedSession)
    observed_at = session.observed_at if retained else _now()
    result = {
        "schema": "submission-policy-context/v1", "submission_repo": repo,
        "snapshot_sha": None, "observed_at": observed_at,
        "read_mode": "retained" if retained else "live",
        "advisory_only": True, "read_operations": 0,
        "provider_requests": 0, "retained_readbacks_used": 0,
        "repository_role": {"status": "NOT_READ", "permissions": {},
                            "source_url": base},
        "snapshot_status": "NOT_READ",
        "documents": [{"path": path, "status": "NOT_READ", "http_status": None,
                       "source_url": base + "/contents/" + quote(path, safe="/"),
                       "git_blob_sha": None,
                       "content_sha256": None, "text": None}
                      for path in POLICY_PATHS],
    }

    def read(url, params=None):
        result["read_operations"] += 1
        if not retained:
            result["provider_requests"] += 1
        response = _read(session, url, headers, params)
        if retained:
            result["retained_readbacks_used"] = session.calls
        return response

    metadata = read(base)
    role = result["repository_role"]
    role.update(status=metadata["status"], http_status=metadata["http_status"])
    if "error" in metadata:
        role["error"] = metadata["error"]
    payload = metadata["payload"]
    if metadata["status"] != "READ" or not isinstance(payload, dict):
        if metadata["status"] == "READ":
            role.update(status="UNAVAILABLE", error="INVALID_REPOSITORY_READBACK")
        return result
    actual_repo = payload.get("full_name", payload.get("repository_full_name"))
    if not isinstance(actual_repo, str) or actual_repo.casefold() != repo.casefold():
        role.update(status="UNAVAILABLE", error="REPOSITORY_TARGET_MISMATCH")
        return result
    permissions = payload.get("permissions")
    role["permissions"] = ({key: value for key, value in permissions.items()
                            if isinstance(key, str) and type(value) is bool}
                           if isinstance(permissions, dict) else {})
    role["permissions_reported"] = isinstance(permissions, dict)
    role["repository_id"] = payload.get("id")
    branch = payload.get("default_branch")
    if metadata["stop"] or not isinstance(branch, str) or not branch:
        return result

    result["snapshot_source_url"] = base + "/git/ref/heads/" + quote(branch, safe="")
    reference = read(result["snapshot_source_url"])
    result["snapshot_status"] = reference["status"]
    if "error" in reference:
        result["snapshot_error"] = reference["error"]
    payload = reference["payload"]
    obj = payload.get("object") if isinstance(payload, dict) else None
    sha = obj.get("sha") if isinstance(obj, dict) else None
    if reference["status"] != "READ" or not isinstance(sha, str) or not _SHA.fullmatch(sha):
        if reference["status"] == "READ":
            result.update(snapshot_status="UNAVAILABLE", snapshot_error="INVALID_REF_READBACK")
        return result
    result["snapshot_sha"] = sha
    for item in result["documents"]:
        item["source_url"] = "https://github.com/" + repo + "/blob/" + sha + "/" + item["path"]
    if reference["stop"]:
        return result

    for item in result["documents"]:
        response = read(base + "/contents/" + quote(item["path"], safe="/"),
                        {"ref": sha})
        item.update(status=response["status"], http_status=response["http_status"])
        if "error" in response:
            item["error"] = response["error"]
        if response["status"] == "READ":
            payload = response["payload"]
            try:
                if not isinstance(payload, dict) or payload.get("path") != item["path"]:
                    raise ValueError("document path mismatch")
                content = payload["content"]
                encoding = payload["encoding"]
                if encoding == "base64" and isinstance(content, str):
                    raw = base64.b64decode("".join(content.split()), validate=True)
                elif encoding == "utf-8" and isinstance(content, str):
                    raw = content.encode("utf-8")
                else:
                    raise ValueError("unsupported document representation")
                if len(raw) > MAX_DOCUMENT_BYTES:
                    raise ValueError("document exceeds size limit")
                blob_sha = payload.get("sha")
                if blob_sha is not None:
                    actual_sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
                    if blob_sha != actual_sha:
                        raise ValueError("document blob digest mismatch")
                item.update(text=raw.decode("utf-8"), git_blob_sha=blob_sha,
                            content_sha256=hashlib.sha256(raw).hexdigest())
            except (KeyError, TypeError, ValueError, UnicodeError):
                item.update(status="UNAVAILABLE", error="INVALID_DOCUMENT_READBACK")
        if response["stop"]:
            break
    return result


def summarize_submission_policy_context(context):
    """Keep document prose in the private sidecar, outside ordinary CLI output."""
    return {
        **{key: value for key, value in context.items() if key != "documents"},
        "documents": [{key: value for key, value in item.items() if key != "text"}
                      for item in context["documents"]],
    }


def write_submission_policy_context(path, context):
    create_exclusive_regular(Path(path),
                             (json.dumps(context, indent=2, sort_keys=True) + "\n").encode(),
                             mode=0o600)


def admit_output_destination(path):
    """Reject known-invalid paths before reads; final creation stays exclusive."""
    parent_fd, leaf = open_verified_parent(Path(path))
    try:
        try:
            os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise SecureOutputError(f"refusing to overwrite or follow output path: {path}")
    finally:
        os.close(parent_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("submission_repo", help="Actual repository receiving the submission")
    parser.add_argument("--retained-input", type=Path,
                        help="Use retained URL/params/provider readbacks; no network")
    parser.add_argument("--out", type=Path, required=True, help="New private sidecar path")
    args = parser.parse_args(argv)
    try:
        validate_submission_repo(args.submission_repo)
        admit_output_destination(args.out)
        if args.retained_input:
            document = json.loads(args.retained_input.read_text(encoding="utf-8"))
            session = RetainedSession(document)
            context = collect_submission_policy_context(args.submission_repo, session=session)
        else:
            import requests
            with requests.Session() as session:
                context = collect_submission_policy_context(
                    args.submission_repo, session=session, token=os.environ.get("GITHUB_TOKEN"))
        write_submission_policy_context(args.out, context)
    except (OSError, ValueError, KeyError, SecureOutputError) as exc:
        parser.error(str(exc))
    print(json.dumps(summarize_submission_policy_context(context), indent=2, sort_keys=True))
    return 0 if (context["snapshot_sha"] is not None and all(
        item["status"] in {"READ", "NOT_FOUND"} for item in context["documents"])) else 2


if __name__ == "__main__":
    raise SystemExit(main())
