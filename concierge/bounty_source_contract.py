# SPDX-License-Identifier: MIT
"""Bounded current-source contract preflight for bounty work orders.

Canonical bounty qualification answers whether work is eligible to dispatch. It
does not prove that an issue's source assumptions are still true. This module
covers the narrow high-confidence case where an intake seat can state those
assumptions explicitly: files that must already exist, plus optional literals
that must still be present inside those files.

The check is intentionally opt-in. It never guesses paths or symbols from an
issue body, never crawls a repository, and never retries provider failures. A
provider ambiguity is an ERROR; a missing required file or literal is STALE.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any
from urllib.parse import quote

import requests

from concierge.config import GITHUB_TOKEN


SCHEMA = "bounty-source-contract/v1"
_MAX_EXPECTATIONS = 32
_MAX_LITERAL_BYTES = 512
_MAX_FILE_BYTES = 1024 * 1024
_TIMEOUT_SECONDS = 20
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class SourceContractError(RuntimeError):
    """The source contract could not be checked safely."""


@dataclass(frozen=True)
class LiteralExpectation:
    path: str
    literal: str


def _normalize_repo(repo: str) -> str:
    value = repo.strip() if isinstance(repo, str) else ""
    if not _REPO_RE.fullmatch(value):
        raise SourceContractError("repo must be in owner/name form")
    return value


def _normalize_ref(ref: str) -> str:
    value = ref.strip() if isinstance(ref, str) else ""
    if not value or len(value) > 256 or any(ord(ch) < 32 for ch in value):
        raise SourceContractError("ref must be a non-empty Git ref or commit SHA")
    return value


def _normalize_path(path: str) -> str:
    value = path.strip() if isinstance(path, str) else ""
    if not value or value.startswith("/") or "\\" in value or "\x00" in value:
        raise SourceContractError("required paths must be relative repository paths")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SourceContractError("required paths must not contain empty, . or .. segments")
    if len(value.encode("utf-8")) > 1024:
        raise SourceContractError("required path exceeds 1024 UTF-8 bytes")
    return value


def _normalize_literal(path: str, literal: str) -> LiteralExpectation:
    normalized_path = _normalize_path(path)
    if not isinstance(literal, str) or not literal:
        raise SourceContractError("required literals must be non-empty strings")
    if len(literal.encode("utf-8")) > _MAX_LITERAL_BYTES:
        raise SourceContractError(
            f"required literal exceeds {_MAX_LITERAL_BYTES} UTF-8 bytes"
        )
    return LiteralExpectation(normalized_path, literal)


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "bounty-concierge-source-contract",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _json_get(
    session: requests.Session,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str] | None = None,
    allow_not_found: bool = False,
) -> Any | None:
    response = None
    try:
        response = session.get(
            url,
            headers=headers,
            params=params,
            timeout=_TIMEOUT_SECONDS,
        )
        if allow_not_found and response.status_code == 404:
            return None
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise SourceContractError(
                f"GitHub returned non-JSON content for {url}"
            ) from exc
    except requests.RequestException as exc:
        raise SourceContractError(f"GitHub read failed for {url}: {exc}") from exc
    finally:
        if response is not None:
            response.close()


def _resolve_ref(
    session: requests.Session,
    repo: str,
    ref: str,
    *,
    headers: dict[str, str],
) -> str:
    encoded_ref = quote(ref, safe="")
    payload = _json_get(
        session,
        f"https://api.github.com/repos/{repo}/commits/{encoded_ref}",
        headers=headers,
    )
    if not isinstance(payload, dict):
        raise SourceContractError("GitHub commit response was not an object")
    sha = payload.get("sha")
    if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
        raise SourceContractError("GitHub commit response did not contain a valid SHA")
    return sha.lower()


def _fetch_file(
    session: requests.Session,
    repo: str,
    path: str,
    resolved_sha: str,
    *,
    headers: dict[str, str],
) -> dict[str, Any] | None:
    encoded_path = quote(path, safe="/")
    payload = _json_get(
        session,
        f"https://api.github.com/repos/{repo}/contents/{encoded_path}",
        headers=headers,
        params={"ref": resolved_sha},
        allow_not_found=True,
    )
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise SourceContractError(f"GitHub contents response for {path} was not a file")
    if payload.get("type") != "file":
        raise SourceContractError(f"required path is not a regular file: {path}")

    sha = payload.get("sha")
    size = payload.get("size")
    if not isinstance(sha, str) or not sha:
        raise SourceContractError(f"GitHub contents response for {path} lacked a blob SHA")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise SourceContractError(f"GitHub contents response for {path} lacked a valid size")

    return {
        "path": path,
        "blob_sha": sha,
        "size": size,
        "encoding": payload.get("encoding"),
        "content": payload.get("content"),
    }


def _decode_text(file_record: dict[str, Any]) -> str:
    path = file_record["path"]
    size = file_record["size"]
    if size > _MAX_FILE_BYTES:
        raise SourceContractError(
            f"literal check file exceeds {_MAX_FILE_BYTES} bytes: {path}"
        )
    encoding = file_record.get("encoding")
    content = file_record.get("content")
    if encoding != "base64" or not isinstance(content, str):
        raise SourceContractError(f"GitHub did not return decodable file content for {path}")
    try:
        cleaned = "".join(content.split())
        raw = base64.b64decode(cleaned, validate=True)
        return raw.decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise SourceContractError(f"required literal file is not valid UTF-8: {path}") from exc


def _receipt_sha(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inspect_source_contract(
    repo: str,
    *,
    ref: str,
    required_files: list[str] | tuple[str, ...] = (),
    required_literals: list[tuple[str, str]] | tuple[tuple[str, str], ...] = (),
    token: str | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Validate explicit current-source assumptions against one resolved commit.

    required_files means the named regular file must already exist.
    required_literals means the named UTF-8 file must already exist and contain
    the exact caller-supplied literal. Literal text is not emitted in the
    receipt; only its SHA-256 is retained.

    No request is retried. Missing requirements yield STALE. Provider ambiguity
    raises SourceContractError so callers fail closed rather than dispatch.
    """

    repo = _normalize_repo(repo)
    ref = _normalize_ref(ref)

    files = tuple(dict.fromkeys(_normalize_path(path) for path in required_files))
    literals = tuple(
        dict.fromkeys(
            _normalize_literal(path, literal) for path, literal in required_literals
        )
    )
    all_paths = tuple(dict.fromkeys([*files, *(item.path for item in literals)]))

    if not all_paths:
        raise SourceContractError("at least one required file or literal is required")
    if len(all_paths) + len(literals) > _MAX_EXPECTATIONS:
        raise SourceContractError(
            f"source contract exceeds {_MAX_EXPECTATIONS} bounded expectations"
        )

    owned_session = session is None
    active_session = session or requests.Session()
    headers = _headers(token if token is not None else GITHUB_TOKEN)

    try:
        resolved_sha = _resolve_ref(active_session, repo, ref, headers=headers)

        fetched: dict[str, dict[str, Any] | None] = {}
        file_checks: list[dict[str, Any]] = []
        reasons: list[str] = []

        for path in all_paths:
            record = _fetch_file(
                active_session,
                repo,
                path,
                resolved_sha,
                headers=headers,
            )
            fetched[path] = record
            exists = record is not None
            file_checks.append(
                {
                    "path": path,
                    "exists": exists,
                    "blob_sha": record["blob_sha"] if record else None,
                    "size": record["size"] if record else None,
                }
            )
            if not exists:
                reasons.append("REQUIRED_FILE_MISSING")

        literal_checks: list[dict[str, Any]] = []
        decoded_cache: dict[str, str] = {}
        for item in literals:
            record = fetched[item.path]
            literal_digest = hashlib.sha256(item.literal.encode("utf-8")).hexdigest()
            if record is None:
                present = False
            else:
                if item.path not in decoded_cache:
                    decoded_cache[item.path] = _decode_text(record)
                present = item.literal in decoded_cache[item.path]
            literal_checks.append(
                {
                    "path": item.path,
                    "literal_sha256": literal_digest,
                    "present": present,
                }
            )
            if not present:
                reasons.append("REQUIRED_LITERAL_MISSING")

        reason_codes = list(dict.fromkeys(reasons))
        ready = not reason_codes
        evidence = {
            "schema": SCHEMA,
            "repo": repo,
            "requested_ref": ref,
            "resolved_sha": resolved_sha,
            "required_files": file_checks,
            "required_literals": literal_checks,
        }
        return {
            **evidence,
            "status": "READY" if ready else "STALE",
            "dispatch": ready,
            "reason_codes": reason_codes,
            "receipt_sha256": _receipt_sha(evidence),
        }
    finally:
        if owned_session:
            active_session.close()


def _parse_literal_arg(value: str) -> tuple[str, str]:
    if "::" not in value:
        raise argparse.ArgumentTypeError(
            "--require-literal must use PATH::LITERAL"
        )
    path, literal = value.split("::", 1)
    try:
        normalized = _normalize_literal(path, literal)
    except SourceContractError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return normalized.path, normalized.literal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate explicit current-source assumptions for a bounty work order "
            "before source mutation."
        )
    )
    parser.add_argument("--repo", required=True, help="GitHub repository in owner/name form")
    parser.add_argument(
        "--ref",
        required=True,
        help="Exact canonical branch/tag/commit to resolve once and bind in the receipt",
    )
    parser.add_argument(
        "--require-file",
        action="append",
        default=[],
        metavar="PATH",
        help="Regular file that must already exist; repeat as needed",
    )
    parser.add_argument(
        "--require-literal",
        action="append",
        default=[],
        type=_parse_literal_arg,
        metavar="PATH::LITERAL",
        help="Exact literal that must still exist in a required UTF-8 file",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = inspect_source_contract(
            args.repo,
            ref=args.ref,
            required_files=args.require_file,
            required_literals=args.require_literal,
        )
    except SourceContractError as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "status": "ERROR",
                        "dispatch": False,
                        "error": str(exc),
                    }
                )
            )
        else:
            print(f"ERROR {exc}")
        return 2

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        reasons = ",".join(result["reason_codes"]) or "none"
        print(
            f"{result['status']} {result['repo']}@{result['resolved_sha']} "
            f"reasons={reasons} receipt={result['receipt_sha256']}"
        )
    return 0 if result["dispatch"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
