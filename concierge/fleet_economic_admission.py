# SPDX-License-Identifier: MIT
"""Canonical-source-hardened fleet economics admission.

The original v1 economics compiler remains byte-preserved in
:mod:`concierge._fleet_economic_admission_v1`. This public surface adds the
source-identity fence that must run before any candidate can contribute to
single or batch economics.

Only bounded HTTPS URLs without whitespace, backslashes, userinfo, query,
fragment, or an explicit port are accepted. GitHub issue URLs bind
case-insensitive owner/repository identity and the exact positive issue number.
Other URLs bind a normalized IDNA host while preserving the path instead of
guessing application-specific identity. This remains planning-only and grants
no dispatch, claim, submission, payment, cash, or revenue authority.
"""
from __future__ import annotations

import argparse
import json
import re
from typing import Any
from urllib.parse import urlsplit

from concierge import _fleet_economic_admission_v1 as _base


EconomicAdmissionInputError = _base.EconomicAdmissionInputError
_MAX_CANONICAL_SOURCE_URL_CHARS = 2048
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_GITHUB_ISSUE_PATH_RE = re.compile(
    r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([0-9]+)(?:/)?$"
)
_HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_host(value: str, field: str) -> str:
    if value.endswith("."):
        raise EconomicAdmissionInputError(
            f"{field} host must not use a trailing-dot alias"
        )
    try:
        host = value.encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise EconomicAdmissionInputError(
            f"{field} host must be valid IDNA"
        ) from exc
    if not host or len(host) > 253:
        raise EconomicAdmissionInputError(f"{field} host is invalid")
    labels = host.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or re.fullmatch(r"[a-z0-9-]+", label) is None
        for label in labels
    ):
        raise EconomicAdmissionInputError(f"{field} host is invalid")
    return host


def _source_identity(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise EconomicAdmissionInputError(f"{field} must be a non-empty string")
    if value != value.strip():
        raise EconomicAdmissionInputError(
            f"{field} must not contain surrounding whitespace"
        )
    source = value
    if len(source) > _MAX_CANONICAL_SOURCE_URL_CHARS:
        raise EconomicAdmissionInputError(
            f"{field} exceeds {_MAX_CANONICAL_SOURCE_URL_CHARS} characters"
        )
    if any(character.isspace() or ord(character) == 0x7F for character in source):
        raise EconomicAdmissionInputError(f"{field} must not contain whitespace")
    if "\\" in source:
        raise EconomicAdmissionInputError(f"{field} must not contain backslashes")
    if "?" in source or "#" in source:
        raise EconomicAdmissionInputError(
            f"{field} must not contain a query or fragment delimiter"
        )

    try:
        parsed = urlsplit(source)
        explicit_port = parsed.port
    except ValueError as exc:
        raise EconomicAdmissionInputError(f"{field} must be a valid URL") from exc

    if parsed.scheme.casefold() != "https":
        raise EconomicAdmissionInputError(f"{field} must use https")
    if parsed.username is not None or parsed.password is not None:
        raise EconomicAdmissionInputError(f"{field} must not contain userinfo")
    if explicit_port is not None or ":" in parsed.netloc:
        raise EconomicAdmissionInputError(f"{field} must not contain an explicit port")
    if parsed.query or parsed.fragment:
        raise EconomicAdmissionInputError(
            f"{field} must not contain a query or fragment"
        )
    if parsed.hostname is None:
        raise EconomicAdmissionInputError(f"{field} must contain a host")

    host = _canonical_host(parsed.hostname, field)
    path = parsed.path or "/"
    if any(segment in {".", ".."} for segment in path.split("/")):
        raise EconomicAdmissionInputError(
            f"{field} path must not contain dot-segment aliases"
        )

    if host in _GITHUB_HOSTS:
        if "%" in path or "//" in path:
            raise EconomicAdmissionInputError(
                f"{field} GitHub path must not contain encoded or repeated-separator aliases"
            )
        match = _GITHUB_ISSUE_PATH_RE.fullmatch(path)
        if match is not None:
            owner, repo, raw_number = match.groups()
            if owner in {".", ".."} or repo in {".", ".."}:
                raise EconomicAdmissionInputError(
                    f"{field} GitHub owner/repository is invalid"
                )
            number = int(raw_number)
            if number <= 0:
                raise EconomicAdmissionInputError(
                    f"{field} GitHub issue number must be positive"
                )
            return f"github-issue:{owner.casefold()}/{repo.casefold()}#{number}"
        host = "github.com"

    return f"url:https://{host}{path}"


def _candidate_source_identities(request: Any) -> list[str]:
    if type(request) is not dict:
        return []
    candidates = request.get("candidates")
    if type(candidates) is not list:
        return []

    identities: list[str] = []
    seen: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        if type(candidate) is not dict:
            continue
        field = f"candidates[{index}].canonical_source_url"
        identity = _source_identity(candidate.get("canonical_source_url"), field)
        prior = seen.get(identity)
        if prior is not None:
            raise EconomicAdmissionInputError(
                "duplicate canonical_source_url identity: "
                f"candidates[{prior}] and candidates[{index}] resolve to {identity}"
            )
        seen[identity] = index
        identities.append(identity)
    return identities


def _verified_receipt_source_identities(receipt: dict[str, Any]) -> list[str]:
    """Reconstruct retained candidate identities without trusting compiler markers."""
    candidates = receipt.get("candidates")
    if type(candidates) is not list:
        raise EconomicAdmissionInputError("receipt candidates must be a list")

    identities: list[str] = []
    seen: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        if type(candidate) is not dict:
            raise EconomicAdmissionInputError(
                f"receipt candidates[{index}] must be an object"
            )
        field = f"candidates[{index}].canonical_source_url"
        identity = _source_identity(candidate.get("canonical_source_url"), field)
        prior = seen.get(identity)
        if prior is not None:
            raise EconomicAdmissionInputError(
                "duplicate canonical_source_url identity: "
                f"candidates[{prior}] and candidates[{index}] resolve to {identity}"
            )
        seen[identity] = index
        identities.append(identity)
    return identities


def compile_fleet_economic_admission(request: dict[str, Any]) -> dict[str, Any]:
    """Compile economics only after canonical source identities are unique."""
    identities = _candidate_source_identities(request)
    receipt = _base.compile_fleet_economic_admission(request)

    body = dict(receipt)
    body.pop("receipt_sha256", None)
    authority = dict(body.get("authority", {}))
    authority["canonical_source_identity_rechecked"] = True
    authority["swarm_bounty_dispatch_authority"] = False
    authority["swarm_bounty_dispatch_rule"] = (
        "generic native-currency economics is analytics only; "
        "new swarm bounty admission requires paid_work_dollar_floor ACTIVE ancestry"
    )
    authority["canonical_source_identity_rule"] = (
        "bounded_https_no_whitespace_backslash_userinfo_query_fragment_or_port;"
        "github_issue_owner_repo_casefold_plus_positive_number;"
        "generic_idna_host_casefold_exact_path"
    )
    body["authority"] = authority
    body["source_identity_sha256"] = _base._sha256_json(sorted(identities))
    return {**body, "receipt_sha256": _base._sha256_json(body)}


def verify_receipt(receipt: dict[str, Any]) -> bool:
    """Verify self-integrity and reconstruct the canonical source commitment."""
    if not _base.verify_receipt(receipt):
        return False
    authority = receipt.get("authority")
    source_digest = receipt.get("source_identity_sha256")
    if (
        type(authority) is not dict
        or authority.get("canonical_source_identity_rechecked") is not True
        or authority.get("swarm_bounty_dispatch_authority") is not False
        or type(authority.get("swarm_bounty_dispatch_rule")) is not str
        or type(source_digest) is not str
        or _HEX_SHA256_RE.fullmatch(source_digest) is None
    ):
        return False
    try:
        identities = _verified_receipt_source_identities(receipt)
    except EconomicAdmissionInputError:
        return False
    return _base._sha256_json(sorted(identities)) == source_digest


def format_summary(receipt: dict[str, Any]) -> str:
    return (
        f"eligible={receipt['economically_eligible_count']} "
        f"hold={receipt['economic_hold_count']} "
        "canonical_source_identity_rechecked=true "
        f"dispatch_authority=false receipt_sha256={receipt['receipt_sha256']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.fleet_economic_admission",
        description=(
            "Apply canonical-source-bound native-currency fleet economics before "
            "expensive paid-work implementation dispatch."
        ),
    )
    parser.add_argument("request", help="request JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true", help="emit full receipt JSON")
    args = parser.parse_args(argv)

    try:
        request = _base._load_request(args.request)
        receipt = compile_fleet_economic_admission(request)
    except (OSError, EconomicAdmissionInputError) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(receipt, sort_keys=True, indent=2))
    else:
        print(format_summary(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())