# SPDX-License-Identifier: MIT
"""Public, host-rooted submission transport failover adapter.

The private routing engine supports dependency injection for isolated tests.
This public module does not: provider-attempt authority comes only from exact
receipt bytes retained in one fixed, root-owned host ledger.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, List, Optional

from concierge import _submission_transport_router_core as _core
from concierge.submission_custody import SubmissionCustodyError, _verify_submission_packet


SubmissionTransportInputError = _core.SubmissionTransportInputError
AuthorityVerifier = _core.AuthorityVerifier

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_HOST_RECEIPT_BYTES = 32 * 1024

_PACKET_ERROR_MESSAGES = {
    "PACKET_DIGEST_MISMATCH": "packet_sha256 does not match canonical packet bytes",
    "PACKET_NOT_READY": "packet is not READY_FOR_HUMAN_SUBMISSION",
    "SOURCE_URL_INVALID": "canonical_source_url is invalid",
    "PACKET_AUTHORITY_MISMATCH": "packet authority is invalid",
}


def _canonical_packet_adapter(packet: Any) -> dict[str, Any]:
    """Validate through submission custody and normalize the fields core needs."""
    try:
        verified = _verify_submission_packet(packet)
    except SubmissionCustodyError as exc:
        detail = _PACKET_ERROR_MESSAGES.get(exc.code, "submission packet rejected")
        raise SubmissionTransportInputError(f"{detail} [{exc.code}]") from exc
    return {
        "canonical_source_url": verified["source_url"],
        "packet_sha256": verified["packet_sha256"],
        "head_sha": verified["head_sha"],
        "artifact_evidence_sha256": verified["artifact_digest"],
    }


# The private engine resolves _check_packet at call time. Its supported public
# adapter always installs the repository's canonical custody verifier.
_core._check_packet = _canonical_packet_adapter


def _secure_root_owned_directory(path: Path) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0:
        return False
    return not bool(st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def _read_host_receipt_from_directory(directory: Path, operation_sha256: str) -> Optional[dict[str, Any]]:
    """Private testable reader. Public compilation never accepts a directory."""
    if type(operation_sha256) is not str or not _SHA256_RE.fullmatch(operation_sha256):
        return None
    if not _secure_root_owned_directory(directory):
        return None

    path = directory / f"{operation_sha256}.json"
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != 0
            or st.st_size <= 0
            or st.st_size > _MAX_HOST_RECEIPT_BYTES
            or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_IROTH)
        ):
            return None
        chunks = []
        remaining = st.st_size
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(fd)

    try:
        text = raw.decode("utf-8")
        value = _core.strict_json_loads(text)
    except (UnicodeError, SubmissionTransportInputError):
        return None
    return value if type(value) is dict else None


def _host_receipt_authority_verifier(receipt: dict[str, Any]) -> bool:
    # Fixed literal trust root: no request, CLI, env, or public function
    # parameter can select another provider-receipt directory.
    retained = _read_host_receipt_from_directory(
        Path("/var/lib/bounty-concierge/provider-attempt-authority"),
        receipt.get("operation_sha256"),
    )
    return retained == receipt


def _stamp_public_authority(receipt: dict[str, Any]) -> dict[str, Any]:
    stamped = dict(receipt)
    authority = dict(stamped["authority"])
    authority.update(
        {
            "provider_authority_trust_root": "fixed-root-owned-provider-receipt-ledger",
            "caller_verifier_injection_supported": False,
            "caller_trust_root_selection_supported": False,
        }
    )
    stamped["authority"] = authority
    stamped.pop("decision_sha256", None)
    stamped["decision_sha256"] = _core._sha256(stamped)
    return stamped


def compile_transport_operation(packet: Any, policy: Any, attempt_index: int) -> dict[str, Any]:
    """Compile the exact operation identity a trusted provider adapter must bind."""
    return _core.compile_transport_operation(packet, policy, attempt_index)


def compile_transport_decision(request: Any) -> dict[str, Any]:
    """Compile through the fixed host receipt ledger; callers cannot inject authority."""
    receipt = _core.compile_transport_decision(
        request, authority_verifier=_host_receipt_authority_verifier
    )
    return _stamp_public_authority(receipt)


def verify_transport_decision(request: Any, receipt: Any) -> bool:
    """Recompile through the same fixed host receipt ledger and compare exact bytes."""
    if type(receipt) is not dict:
        return False
    try:
        expected = compile_transport_decision(request)
    except SubmissionTransportInputError:
        return False
    return expected == receipt


strict_json_loads = _core.strict_json_loads
format_summary = _core.format_summary

__all__ = [
    "AuthorityVerifier",
    "SubmissionTransportInputError",
    "compile_transport_operation",
    "compile_transport_decision",
    "verify_transport_decision",
    "strict_json_loads",
    "format_summary",
    "main",
]


def _load_request(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    value = strict_json_loads(raw)
    if type(value) is not dict:
        raise SubmissionTransportInputError("request JSON must contain an object")
    return value


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.submission_transport_router",
        description="Compile a send-free transport decision through the fixed host receipt ledger.",
    )
    parser.add_argument("request", help="JSON request path, or - for stdin")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = compile_transport_decision(_load_request(args.request))
    except (OSError, SubmissionTransportInputError) as exc:
        parser.error(str(exc))
    if args.summary:
        print(format_summary(receipt))
    else:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["disposition"] in {"READY_PRIMARY", "READY_FALLBACK", "ALREADY_SUBMITTED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
