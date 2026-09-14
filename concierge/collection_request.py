# SPDX-License-Identifier: MIT
"""Deterministic, evidence-bound sponsor collection request packets.

This module turns an already-merged paid-work record into a direct, send-ready
*assessment request*, or (only with separate acceptance/award evidence) a
*payment request*. It never sends the request and never treats merge state as
proof of acceptance, debt, earned revenue, or payment due.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_SCHEMA = "bounty-collection-request/v1"
_INPUT_SCHEMA = "bounty-collection-request-input/v1"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9_.-]{1,11}$")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ROUTE_TYPES = frozenset({"HOSTED_HANDLE", "PAYMENT_LINK", "WALLET", "OTHER"})
_ACCEPTANCE_KINDS = frozenset({"NONE", "SPONSOR_ACCEPTED", "AWARDED"})
_MAX_JSON_BYTES = 1024 * 1024
_MAX_TEXT = 4096
_READ_CHUNK_BYTES = 64 * 1024


class CollectionRequestInputError(ValueError):
    """Malformed, ambiguous, or authority-expanding input."""


def _canonical_json(value: Any) -> bytes:
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CollectionRequestInputError("value is not canonical JSON") from exc
    if len(payload) > _MAX_JSON_BYTES:
        raise CollectionRequestInputError("canonical JSON is too large")
    return payload


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _strict_object(value: Any, *, keys: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise CollectionRequestInputError(f"{field} must contain exactly {sorted(keys)}")
    return value


def _text(value: Any, *, field: str, max_len: int = 512) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > max_len:
        raise CollectionRequestInputError(f"{field} must be non-empty bounded text without edge whitespace")
    if any(ord(ch) < 32 and ch not in "\t" for ch in value):
        raise CollectionRequestInputError(f"{field} contains control characters")
    return value


def _url(value: Any, *, field: str) -> str:
    text = _text(value, field=field, max_len=2048)
    parsed = urlsplit(text)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise CollectionRequestInputError(f"{field} must be an absolute credential-free HTTPS URL")
    return text


def _amount(value: Any) -> tuple[Decimal, str]:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise CollectionRequestInputError("work.advertised_amount must be a positive decimal")
    source = str(value)
    if len(source) > 64:
        raise CollectionRequestInputError("work.advertised_amount is too large")
    try:
        amount = Decimal(source)
    except (InvalidOperation, ValueError) as exc:
        raise CollectionRequestInputError("work.advertised_amount must be a positive decimal") from exc
    exp = amount.as_tuple().exponent
    if not amount.is_finite() or amount <= 0 or len(amount.as_tuple().digits) > 30 or not isinstance(exp, int) or abs(exp) > 18:
        raise CollectionRequestInputError("work.advertised_amount must be a bounded positive decimal")
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return amount, text


def _validate_input(payload: Any) -> dict[str, Any]:
    root = _strict_object(payload, keys={"schema", "sponsor_name", "work", "payout_route", "acceptance"}, field="input")
    if root["schema"] != _INPUT_SCHEMA:
        raise CollectionRequestInputError(f"input.schema must be {_INPUT_SCHEMA}")
    sponsor_name = _text(root["sponsor_name"], field="sponsor_name", max_len=160)

    work = _strict_object(root["work"], keys={"repo", "pr", "canonical_url", "head_sha", "state", "advertised_amount", "currency"}, field="work")
    repo = _text(work["repo"], field="work.repo", max_len=200)
    if not _REPO_RE.fullmatch(repo) or any(part in {".", ".."} for part in repo.split("/")):
        raise CollectionRequestInputError("work.repo must be owner/name")
    pr = work["pr"]
    if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
        raise CollectionRequestInputError("work.pr must be a positive integer")
    canonical_url = _url(work["canonical_url"], field="work.canonical_url")
    if canonical_url != f"https://github.com/{repo}/pull/{pr}":
        raise CollectionRequestInputError("work.canonical_url must match repo/pr exactly")
    head_sha = _text(work["head_sha"], field="work.head_sha", max_len=40)
    if not _SHA40_RE.fullmatch(head_sha):
        raise CollectionRequestInputError("work.head_sha must be lowercase 40-hex")
    if work["state"] != "MERGED":
        raise CollectionRequestInputError("work.state must be MERGED")
    _, amount_text = _amount(work["advertised_amount"])
    currency = _text(work["currency"], field="work.currency", max_len=12)
    if not _CURRENCY_RE.fullmatch(currency):
        raise CollectionRequestInputError("work.currency must be an uppercase currency/token code")

    route = _strict_object(root["payout_route"], keys={"type", "value"}, field="payout_route")
    route_type = _text(route["type"], field="payout_route.type", max_len=32)
    if route_type not in _ROUTE_TYPES:
        raise CollectionRequestInputError(f"payout_route.type must be one of {sorted(_ROUTE_TYPES)}")
    route_value = _text(route["value"], field="payout_route.value", max_len=512)
    if route_type == "PAYMENT_LINK":
        _url(route_value, field="payout_route.value")

    acceptance = _strict_object(root["acceptance"], keys={"kind", "evidence_ref", "evidence_sha256"}, field="acceptance")
    kind = _text(acceptance["kind"], field="acceptance.kind", max_len=32)
    if kind not in _ACCEPTANCE_KINDS:
        raise CollectionRequestInputError(f"acceptance.kind must be one of {sorted(_ACCEPTANCE_KINDS)}")
    evidence_ref = acceptance["evidence_ref"]
    evidence_sha256 = acceptance["evidence_sha256"]
    if kind == "NONE":
        if evidence_ref is not None or evidence_sha256 is not None:
            raise CollectionRequestInputError("NONE acceptance must not carry acceptance evidence")
    else:
        evidence_ref = _url(evidence_ref, field="acceptance.evidence_ref")
        evidence_sha256 = _text(evidence_sha256, field="acceptance.evidence_sha256", max_len=64)
        if not _SHA256_RE.fullmatch(evidence_sha256):
            raise CollectionRequestInputError("acceptance.evidence_sha256 must be lowercase 64-hex")

    return {
        "schema": _INPUT_SCHEMA,
        "sponsor_name": sponsor_name,
        "work": {"repo": repo, "pr": pr, "canonical_url": canonical_url, "head_sha": head_sha, "state": "MERGED", "advertised_amount": amount_text, "currency": currency},
        "payout_route": {"type": route_type, "value": route_value},
        "acceptance": {"kind": kind, "evidence_ref": evidence_ref, "evidence_sha256": evidence_sha256},
    }


def _compose(normalized: dict[str, Any]) -> tuple[str, str, str]:
    work = normalized["work"]
    sponsor = normalized["sponsor_name"]
    route = normalized["payout_route"]
    acceptance = normalized["acceptance"]
    amount = f"{work['advertised_amount']} {work['currency']}"
    ref = f"{work['repo']}#{work['pr']}"
    if acceptance["kind"] == "NONE":
        disposition = "READY_TO_REQUEST_ASSESSMENT"
        subject = f"{ref} — request for assessment under advertised {amount} terms"
        body = (
            f"Hi {sponsor},\n\n"
            f"I’m requesting assessment of my completed contribution under the advertised {amount} terms.\n\n"
            f"Merged work: {work['canonical_url']}\n"
            f"Exact submitted head: {work['head_sha']}\n"
            f"Advertised reward: {amount}\n"
            f"Payout route if accepted: {route['type']} — {route['value']}\n\n"
            "Please confirm acceptance and, if accepted, arrange the advertised payout through the route above.\n\n"
            "Thanks,\nBryce"
        )
    else:
        disposition = "READY_TO_REQUEST_PAYMENT"
        subject = f"{ref} — payment request for accepted {amount} contribution"
        body = (
            f"Hi {sponsor},\n\n"
            f"I’m requesting the advertised {amount} payout for the accepted contribution below.\n\n"
            f"Merged work: {work['canonical_url']}\n"
            f"Exact submitted head: {work['head_sha']}\n"
            f"Acceptance evidence: {acceptance['evidence_ref']}\n"
            f"Payout route: {route['type']} — {route['value']}\n\n"
            "Please arrange the advertised payout through the route above and confirm when it has been initiated.\n\n"
            "Thanks,\nBryce"
        )
    if len(subject) > 998 or len(body) > _MAX_TEXT:
        raise CollectionRequestInputError("rendered collection request is too large")
    return disposition, subject, body


def compile_collection_request(payload: Any) -> dict[str, Any]:
    """Compile a deterministic, send-ready draft with a fail-closed authority ceiling."""
    normalized = _validate_input(payload)
    disposition, subject, body = _compose(normalized)
    core = {
        "schema": _SCHEMA,
        "disposition": disposition,
        "subject": subject,
        "body": body,
        "source": normalized,
        "authority": {
            "external_send": False,
            "provider_mutation": False,
            "payment_mutation": False,
            "merge_proves_acceptance": False,
            "advertised_reward_is_debt": False,
            "advertised_reward_is_earned_revenue": False,
            "payment_due_claim": False,
            "draft_requires_human_or_separate_sender": True,
        },
    }
    packet = dict(core)
    packet["receipt_sha256"] = _sha256(core)
    return packet


def verify_collection_request(payload: Any, packet: Any) -> bool:
    """Verify exact deterministic derivation, including the whole authority ceiling."""
    if not isinstance(packet, dict):
        return False
    try:
        expected = compile_collection_request(payload)
    except CollectionRequestInputError:
        return False
    return packet == expected


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise CollectionRequestInputError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _lstat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise CollectionRequestInputError("input path could not be inspected safely") from exc


def _stat_generation(value: os.stat_result) -> tuple[int, ...]:
    """Return the mutation-sensitive regular-file generation used for custody."""
    fields = (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        getattr(value, "st_mtime_ns", int(value.st_mtime * 1_000_000_000)),
        getattr(value, "st_ctime_ns", int(value.st_ctime * 1_000_000_000)),
    )
    if any(isinstance(item, bool) or not isinstance(item, int) for item in fields):
        raise CollectionRequestInputError("input file generation metadata is unavailable")
    if value.st_ino == 0 or value.st_nlink <= 0 or value.st_size < 0:
        raise CollectionRequestInputError("input file lacks stable regular-file identity")
    return fields


def _require_regular(value: os.stat_result) -> tuple[int, ...]:
    if not stat.S_ISREG(value.st_mode):
        raise CollectionRequestInputError("input path must name a regular file")
    return _stat_generation(value)


def _open_regular_file(path: Path) -> int:
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NONBLOCK", "O_NOFOLLOW", "O_BINARY"):
        flags |= getattr(os, name, 0)
    try:
        return os.open(path, flags)
    except OSError as exc:
        raise CollectionRequestInputError("input path could not be opened safely") from exc


def _read_regular_file(path: Path) -> bytes:
    """Capture one bounded, stable caller-visible regular-file generation."""
    before_path = _lstat(path)
    before_path_generation = _require_regular(before_path)

    descriptor = _open_regular_file(path)
    try:
        try:
            before_descriptor = os.fstat(descriptor)
        except OSError as exc:
            raise CollectionRequestInputError("input file descriptor could not be inspected") from exc
        before_descriptor_generation = _require_regular(before_descriptor)
        if before_descriptor_generation != before_path_generation:
            raise CollectionRequestInputError("input path changed while opening")
        if before_descriptor.st_size > _MAX_JSON_BYTES:
            raise CollectionRequestInputError("input file is too large")

        chunks: list[bytes] = []
        total = 0
        while total < _MAX_JSON_BYTES + 1:
            try:
                chunk = os.read(
                    descriptor,
                    min(_READ_CHUNK_BYTES, _MAX_JSON_BYTES + 1 - total),
                )
            except OSError as exc:
                raise CollectionRequestInputError("input file could not be read safely") from exc
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)

        if total > _MAX_JSON_BYTES:
            raise CollectionRequestInputError("input file is too large")
        raw = b"".join(chunks)

        try:
            after_descriptor = os.fstat(descriptor)
        except OSError as exc:
            raise CollectionRequestInputError("input file descriptor could not be re-inspected") from exc
        after_descriptor_generation = _require_regular(after_descriptor)
        if after_descriptor_generation != before_descriptor_generation:
            raise CollectionRequestInputError("input file changed while reading")
        if len(raw) != after_descriptor.st_size:
            raise CollectionRequestInputError("input file length changed while reading")

        after_path = _lstat(path)
        after_path_generation = _require_regular(after_path)
        if after_path_generation != after_descriptor_generation:
            raise CollectionRequestInputError("input path changed while reading")

        try:
            final_descriptor = os.fstat(descriptor)
        except OSError as exc:
            raise CollectionRequestInputError("input file descriptor could not be finalized") from exc
        final_descriptor_generation = _require_regular(final_descriptor)
        if final_descriptor_generation != after_descriptor_generation:
            raise CollectionRequestInputError("input file changed during final path validation")
        return raw
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def load_json(path: Path) -> Any:
    raw = _read_regular_file(path)
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(CollectionRequestInputError(f"non-finite JSON constant: {value}")),
        )
    except UnicodeDecodeError as exc:
        raise CollectionRequestInputError("input file must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise CollectionRequestInputError("input file must contain valid JSON") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m concierge.collection_request")
    parser.add_argument("input", type=Path)
    parser.add_argument("--verify", type=Path, help="verify an existing packet instead of compiling")
    args = parser.parse_args(argv)
    try:
        payload = load_json(args.input)
        if args.verify is not None:
            packet = load_json(args.verify)
            ok = verify_collection_request(payload, packet)
            print(json.dumps({"verified": ok}, sort_keys=True))
            return 0 if ok else 3
        print(json.dumps(compile_collection_request(payload), indent=2, sort_keys=True))
        return 0
    except (CollectionRequestInputError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
