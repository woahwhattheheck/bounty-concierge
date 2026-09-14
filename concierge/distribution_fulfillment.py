# SPDX-License-Identifier: MIT
"""Fail-closed readiness gate for paid work requiring live distribution.

This module is evidence-only.  It never posts content, submits a claim, infers
maintainer acceptance, authorizes payout, or recognizes revenue.

Decision-driving evidence is split across two trust planes:

* ``request`` is untrusted/auditable business evidence;
* ``authority`` is a MAC-authenticated record issued by a retained verifier
  under a host key that is not supplied by the request/authority CLI caller.

The production CLI never accepts an authority path or key.  It derives the
record location from the canonical source URL and reads the active key from a
fixed operator-local path.  Library integrations may pass an already-retained
authority record and trusted key directly.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit


class DistributionFulfillmentError(ValueError):
    pass


ISSUE_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)$")
COMMENT_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)#issuecomment-([1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511}$")
NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
HEX_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
REWARD_RE = re.compile(r"^(?:0|[1-9][0-9]{0,63})(?:\.[0-9]{1,64})?$")
BLOCKED_HOSTS = {"github.com", "www.github.com", "gist.github.com"}
MAX_AGE_SECONDS = 7 * 24 * 60 * 60
REQUEST_SCHEMA = "distribution-fulfillment-request/v1"
RECEIPT_SCHEMA = "distribution-fulfillment-receipt/v3"
AUTHORITY_SCHEMA = "distribution-fulfillment-authority/v2"
AUTHORITY_KEY_SCHEMA = "distribution-fulfillment-authority-key/v1"
QUEUE_SCHEMA = "distribution-fulfillment-queue/v3"
QUEUE_ITEM_SCHEMA = "distribution-fulfillment-queue-item/v2"
AUTHORITY_MAC_CONTEXT = b"bounty-concierge/distribution-fulfillment-authority/v2\x00"
DEFAULT_AUTHORITY_ROOT = (
    Path(r"C:\\ProgramData\\bounty-concierge\\distribution-fulfillment")
    if os.name == "nt"
    else Path("/var/lib/bounty-concierge/distribution-fulfillment")
)
DEFAULT_AUTHORITY_KEY_PATH = DEFAULT_AUTHORITY_ROOT / "authority-key.json"
DEFAULT_AUTHORITY_RECORD_DIR = DEFAULT_AUTHORITY_ROOT / "records"


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DistributionFulfillmentError("value is not canonical JSON") from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _exact_json(left: Any, right: Any) -> bool:
    try:
        return _json(left) == _json(right)
    except DistributionFulfillmentError:
        return False


def _obj(value: Any, keys: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise DistributionFulfillmentError(f"{name} has missing or undeclared fields")
    return value


def _text(value: Any, name: str, limit: int = 4096) -> str:
    if type(value) is not str or not value or len(value) > limit:
        raise DistributionFulfillmentError(f"{name} must be a non-empty bounded string")
    if "\x00" in value or any(ord(ch) < 32 for ch in value):
        raise DistributionFulfillmentError(f"{name} contains control characters")
    return value


def _sha(value: Any, name: str) -> str:
    if type(value) is not str or not SHA_RE.fullmatch(value):
        raise DistributionFulfillmentError(f"{name} must be 64 lowercase hex characters")
    return value


def _ts(value: Any, name: str) -> datetime:
    text = _text(value, name, 64)
    if not text.endswith("Z"):
        raise DistributionFulfillmentError(f"{name} must be canonical UTC")
    try:
        dt = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise DistributionFulfillmentError(f"{name} is not ISO-8601") from exc
    canonical = dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0) or text != canonical:
        raise DistributionFulfillmentError(f"{name} must be canonical UTC second precision")
    return dt


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _trusted_now(value: Optional[str]) -> tuple[str, datetime]:
    if value is None:
        value = _fmt(datetime.now(timezone.utc))
    return value, _ts(value, "trusted_at")


def _reward(value: Any) -> tuple[Decimal, str]:
    if type(value) is not str or not value or len(value) > 128 or not REWARD_RE.fullmatch(value):
        raise DistributionFulfillmentError("advertised_reward must be a bounded plain decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise DistributionFulfillmentError("advertised_reward must be a decimal string") from exc
    # REWARD_RE excludes exponent notation before Decimal formatting, preventing
    # attacker-sized fixed-point expansion such as 1e100000000.
    canonical = format(parsed, "f")
    if not parsed.is_finite() or parsed < 0 or len(parsed.as_tuple().digits) > 64 or len(canonical) > 128:
        raise DistributionFulfillmentError("advertised_reward must be finite, non-negative, and bounded")
    return parsed, canonical


def _issue(value: Any) -> str:
    text = _text(value, "canonical_source_url")
    match = ISSUE_RE.fullmatch(text)
    if not match or any(part in {".", ".."} for part in match.groups()[:2]):
        raise DistributionFulfillmentError("canonical_source_url is not canonical")
    owner, repo, issue = match.groups()
    # GitHub owner/repository routing is case-insensitive.  Normalize that
    # identity before retained-record hashing, queue dedupe, and receipts so
    # case aliases cannot represent one bounty as multiple claim units.
    return f"https://github.com/{owner.casefold()}/{repo.casefold()}/issues/{issue}"


def _comment(value: Any, source: str) -> str:
    text = _text(value, "maintainer_rule.comment_url")
    match, src = COMMENT_RE.fullmatch(text), ISSUE_RE.fullmatch(source)
    if not match or not src:
        raise DistributionFulfillmentError("maintainer rule comment URL is not canonical")
    if tuple(x.casefold() for x in match.groups()[:3]) != tuple(x.casefold() for x in src.groups()):
        raise DistributionFulfillmentError("maintainer rule comment must belong to source issue")
    return f"{source}#issuecomment-{match.group(4)}"


def _host(value: Any, name: str) -> str:
    host = _text(value, name, 253).casefold().rstrip(".")
    labels = host.split(".")
    if len(labels) < 2 or any(not HOST_LABEL_RE.fullmatch(label) for label in labels):
        raise DistributionFulfillmentError(f"{name} is not a canonical hostname")
    return host


def _live_url(value: Any, name: str) -> tuple[str, str]:
    text = _text(value, name)
    parsed = urlsplit(text)
    try:
        port = parsed.port
    except ValueError as exc:
        raise DistributionFulfillmentError(f"{name} has an invalid port") from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not parsed.hostname
        or parsed.fragment
    ):
        raise DistributionFulfillmentError(f"{name} must be canonical public HTTPS")
    host = _host(parsed.hostname, f"{name} host")
    if host in BLOCKED_HOSTS or host.endswith(".github.com"):
        raise DistributionFulfillmentError(f"{name} must be off-platform, not GitHub-hosted")
    canonical = urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))
    if canonical != text:
        raise DistributionFulfillmentError(f"{name} must use canonical HTTPS spelling")
    return text, host


def _host_ok(host: str, allowed: str) -> bool:
    return host == allowed or host.endswith("." + allowed)


def _source(raw: Any, url: str) -> dict[str, Any]:
    raw = _obj(raw, {"state", "labels", "updated_at", "captured_at", "snapshot_sha256"}, "source")
    if raw["state"] not in {"open", "closed"}:
        raise DistributionFulfillmentError("source.state is invalid")
    labels = raw["labels"]
    if type(labels) is not list or not labels or len(labels) > 128:
        raise DistributionFulfillmentError("source.labels must be a non-empty bounded list")
    checked = [_text(value, "source label", 128) for value in labels]
    folded = [value.casefold() for value in checked]
    if len(folded) != len(set(folded)):
        raise DistributionFulfillmentError("source.labels contains duplicates")
    if _ts(raw["updated_at"], "source.updated_at") > _ts(raw["captured_at"], "source.captured_at"):
        raise DistributionFulfillmentError("source snapshot predates source update")
    return {
        "canonical_source_url": url,
        "state": raw["state"],
        "labels": sorted(checked, key=str.casefold),
        "updated_at": raw["updated_at"],
        "captured_at": raw["captured_at"],
        "snapshot_sha256": _sha(raw["snapshot_sha256"], "source.snapshot_sha256"),
    }


def _rule(raw: Any, url: str) -> dict[str, Any]:
    raw = _obj(
        raw,
        {"comment_url", "comment_sha256", "captured_at", "requires_live_url", "allowed_hosts", "rule_text_sha256"},
        "maintainer_rule",
    )
    if type(raw["requires_live_url"]) is not bool:
        raise DistributionFulfillmentError("maintainer_rule.requires_live_url must be boolean")
    hosts = raw["allowed_hosts"]
    if type(hosts) is not list or not hosts or len(hosts) > 128:
        raise DistributionFulfillmentError("maintainer_rule.allowed_hosts must be a non-empty bounded list")
    checked = [_host(value, "allowed host") for value in hosts]
    if len(checked) != len(set(checked)):
        raise DistributionFulfillmentError("maintainer_rule.allowed_hosts contains duplicates")
    if any(value in BLOCKED_HOSTS or value.endswith(".github.com") for value in checked):
        raise DistributionFulfillmentError("GitHub cannot satisfy off-platform delivery")
    _ts(raw["captured_at"], "maintainer_rule.captured_at")
    return {
        "comment_url": _comment(raw["comment_url"], url),
        "comment_sha256": _sha(raw["comment_sha256"], "maintainer_rule.comment_sha256"),
        "captured_at": raw["captured_at"],
        "requires_live_url": raw["requires_live_url"],
        "allowed_hosts": sorted(checked),
        "rule_text_sha256": _sha(raw["rule_text_sha256"], "maintainer_rule.rule_text_sha256"),
    }


def _packet(raw: Any, url: str) -> dict[str, Any]:
    raw = _obj(raw, {"schema", "canonical_source_url", "disposition", "packet_sha256", "authority"}, "submission_packet")
    if raw["schema"] != "bounty-submission-packet-item/v1":
        raise DistributionFulfillmentError("submission_packet schema is unsupported")
    if _issue(raw["canonical_source_url"]) != url:
        raise DistributionFulfillmentError("submission_packet source does not match canonical source")
    if raw["disposition"] not in {"READY_FOR_HUMAN_SUBMISSION", "HOLD"}:
        raise DistributionFulfillmentError("submission_packet disposition is invalid")
    authority = _obj(raw["authority"], {"submission", "acceptance", "payout", "cash_claim"}, "submission_packet.authority")
    if (
        type(authority["cash_claim"]) is not bool
        or authority["cash_claim"] is not False
        or type(authority["submission"]) is not str
        or type(authority["acceptance"]) is not str
        or type(authority["payout"]) is not str
        or authority["submission"] != "human_only"
        or authority["acceptance"] != "not_inferred"
        or authority["payout"] != "not_inferred"
    ):
        raise DistributionFulfillmentError("submission_packet authority exceeds boundary")
    return {
        "schema": raw["schema"],
        "canonical_source_url": url,
        "disposition": raw["disposition"],
        "packet_sha256": _sha(raw["packet_sha256"], "submission_packet.packet_sha256"),
        "authority": authority,
    }


def _capture(raw: Any, url: str) -> dict[str, Any]:
    keys = {
        "canonical_source_url",
        "live_url",
        "resolved_url",
        "resource_identity",
        "observed_at",
        "http_status",
        "publicly_resolvable",
        "content_sha256",
        "capture_sha256",
        "verifier",
    }
    raw = _obj(raw, keys, "live_capture")
    if _issue(raw["canonical_source_url"]) != url:
        raise DistributionFulfillmentError("live capture source mismatch")
    live, live_host = _live_url(raw["live_url"], "live_capture.live_url")
    resolved, resolved_host = _live_url(raw["resolved_url"], "live_capture.resolved_url")
    resource_identity = _text(raw["resource_identity"], "live_capture.resource_identity", 512)
    if not RESOURCE_RE.fullmatch(resource_identity):
        raise DistributionFulfillmentError("live_capture.resource_identity is not canonical")
    status = raw["http_status"]
    if type(status) is not int or isinstance(status, bool) or not 100 <= status <= 599:
        raise DistributionFulfillmentError("live_capture.http_status is invalid")
    if type(raw["publicly_resolvable"]) is not bool:
        raise DistributionFulfillmentError("live_capture.publicly_resolvable must be boolean")
    _ts(raw["observed_at"], "live_capture.observed_at")
    core = {
        "canonical_source_url": url,
        "live_url": live,
        "resolved_url": resolved,
        "resource_identity": resource_identity,
        "observed_at": raw["observed_at"],
        "http_status": status,
        "publicly_resolvable": raw["publicly_resolvable"],
        "content_sha256": _sha(raw["content_sha256"], "live_capture.content_sha256"),
        "verifier": _text(raw["verifier"], "live_capture.verifier", 128),
    }
    digest = _sha(raw["capture_sha256"], "live_capture.capture_sha256")
    if not hmac.compare_digest(_hash(core), digest):
        raise DistributionFulfillmentError("live_capture.capture_sha256 does not bind capture")
    return {**core, "capture_sha256": digest, "_live_host": live_host, "_resolved_host": resolved_host}


def _normalize_request(request: Any) -> tuple[str, Decimal, str, dict[str, Any], dict[str, Any], dict[str, Any], Optional[dict[str, Any]]]:
    request = _obj(
        request,
        {"schema", "canonical_source_url", "advertised_reward", "source", "maintainer_rule", "submission_packet", "live_capture"},
        "request",
    )
    if request["schema"] != REQUEST_SCHEMA:
        raise DistributionFulfillmentError("request schema is unsupported")
    url = _issue(request["canonical_source_url"])
    reward, reward_text = _reward(request["advertised_reward"])
    source = _source(request["source"], url)
    rule = _rule(request["maintainer_rule"], url)
    packet = _packet(request["submission_packet"], url)
    capture = None if request["live_capture"] is None else _capture(request["live_capture"], url)
    return url, reward, reward_text, source, rule, packet, capture


def _authority_unsigned(
    *,
    url: str,
    advertised_reward: str,
    source: dict[str, Any],
    rule: dict[str, Any],
    packet: dict[str, Any],
    capture: Optional[dict[str, Any]],
    provider_namespace: Optional[str],
    key_id: str,
    issued_at: str,
) -> dict[str, Any]:
    if not KEY_ID_RE.fullmatch(_text(key_id, "authority.key_id", 128)):
        raise DistributionFulfillmentError("authority.key_id is not canonical")
    _ts(issued_at, "authority.issued_at")
    public_capture = None if capture is None else {key: value for key, value in capture.items() if not key.startswith("_")}
    if public_capture is None:
        if provider_namespace is not None:
            raise DistributionFulfillmentError("authority.provider_namespace must be null without capture")
        capture_verifier = None
        resource_identity = None
        content_sha256 = None
    else:
        provider_namespace = _text(provider_namespace, "authority.provider_namespace", 128)
        if not NAMESPACE_RE.fullmatch(provider_namespace):
            raise DistributionFulfillmentError("authority.provider_namespace is not canonical")
        capture_verifier = public_capture["verifier"]
        resource_identity = public_capture["resource_identity"]
        content_sha256 = public_capture["content_sha256"]
    return {
        "schema": AUTHORITY_SCHEMA,
        "canonical_source_url": url,
        "advertised_reward": advertised_reward,
        "source_decision_sha256": _hash(source),
        "maintainer_rule_decision_sha256": _hash(rule),
        "submission_packet_decision_sha256": _hash(packet),
        "live_capture_decision_sha256": None if public_capture is None else _hash(public_capture),
        "provider_namespace": provider_namespace,
        "resource_identity": resource_identity,
        "content_sha256": content_sha256,
        "capture_verifier": capture_verifier,
        "issued_at": issued_at,
        "key_id": key_id,
    }


def _authority_mac(unsigned: dict[str, Any], key: bytes) -> str:
    if type(key) is not bytes or len(key) != 32:
        raise DistributionFulfillmentError("trusted authority key must be exactly 32 bytes")
    body = AUTHORITY_MAC_CONTEXT + _json(unsigned).encode()
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def issue_authority_record(
    request: Any,
    *,
    provider_namespace: Optional[str],
    trusted_key: bytes,
    key_id: str,
    issued_at: str,
) -> dict[str, Any]:
    """Issue a retained authority record from a trusted verifier boundary.

    This function requires the host verifier secret explicitly.  The production
    CLI deliberately exposes no signing command and never accepts a caller key.
    """
    url, _reward_value, reward_text, source, rule, packet, capture = _normalize_request(request)
    unsigned = _authority_unsigned(
        url=url,
        advertised_reward=reward_text,
        source=source,
        rule=rule,
        packet=packet,
        capture=capture,
        provider_namespace=provider_namespace,
        key_id=key_id,
        issued_at=issued_at,
    )
    return {**unsigned, "mac_sha256": _authority_mac(unsigned, trusted_key)}


def _verify_authority(
    raw: Any,
    *,
    url: str,
    advertised_reward: str,
    source: dict[str, Any],
    rule: dict[str, Any],
    packet: dict[str, Any],
    capture: Optional[dict[str, Any]],
    trusted_key: bytes,
    trusted_key_id: str,
    trusted_at: datetime,
) -> dict[str, Any]:
    keys = {
        "schema",
        "canonical_source_url",
        "advertised_reward",
        "source_decision_sha256",
        "maintainer_rule_decision_sha256",
        "submission_packet_decision_sha256",
        "live_capture_decision_sha256",
        "provider_namespace",
        "resource_identity",
        "content_sha256",
        "capture_verifier",
        "issued_at",
        "key_id",
        "mac_sha256",
    }
    raw = _obj(raw, keys, "authority")
    if raw["schema"] != AUTHORITY_SCHEMA or raw["canonical_source_url"] != url:
        raise DistributionFulfillmentError("authority does not match request source")
    if raw["key_id"] != trusted_key_id:
        raise DistributionFulfillmentError("authority key_id is not the active trusted key")
    issued = _ts(raw["issued_at"], "authority.issued_at")
    if issued > trusted_at:
        raise DistributionFulfillmentError("authority cannot be issued in the future")

    expected = _authority_unsigned(
        url=url,
        advertised_reward=advertised_reward,
        source=source,
        rule=rule,
        packet=packet,
        capture=capture,
        provider_namespace=raw["provider_namespace"],
        key_id=trusted_key_id,
        issued_at=raw["issued_at"],
    )
    supplied_mac = _sha(raw["mac_sha256"], "authority.mac_sha256")
    if not hmac.compare_digest(_authority_mac(expected, trusted_key), supplied_mac):
        raise DistributionFulfillmentError("authority MAC authentication failed")

    for key in (
        "advertised_reward",
        "source_decision_sha256",
        "maintainer_rule_decision_sha256",
        "submission_packet_decision_sha256",
        "live_capture_decision_sha256",
        "provider_namespace",
        "resource_identity",
        "content_sha256",
        "capture_verifier",
    ):
        if not _exact_json(raw[key], expected[key]):
            raise DistributionFulfillmentError(f"authority.{key} does not bind decision evidence")
    return dict(raw)


def _evaluate(
    request: Any,
    *,
    authority: Any,
    trusted_key: bytes,
    trusted_key_id: str,
    trusted_at: str,
) -> dict[str, Any]:
    url, reward, reward_text, source, rule, packet, capture = _normalize_request(request)
    now = _ts(trusted_at, "trusted_at")
    retained = _verify_authority(
        authority,
        url=url,
        advertised_reward=reward_text,
        source=source,
        rule=rule,
        packet=packet,
        capture=capture,
        trusted_key=trusted_key,
        trusted_key_id=trusted_key_id,
        trusted_at=now,
    )

    source_time = _ts(source["captured_at"], "source.captured_at")
    rule_time = _ts(rule["captured_at"], "maintainer_rule.captured_at")
    if source_time > now or rule_time > now:
        raise DistributionFulfillmentError("captured evidence cannot be from future")
    issued_time = _ts(retained["issued_at"], "authority.issued_at")
    if issued_time < max(source_time, rule_time):
        raise DistributionFulfillmentError("authority predates source or maintainer-rule evidence")

    reasons: list[str] = []
    if (now - source_time).total_seconds() > MAX_AGE_SECONDS:
        reasons.append("SOURCE_SNAPSHOT_STALE")
    if (now - rule_time).total_seconds() > MAX_AGE_SECONDS:
        reasons.append("MAINTAINER_RULE_STALE")
    if source["state"] != "open":
        reasons.append("SOURCE_NOT_OPEN")
    if "distribution" not in {value.casefold() for value in source["labels"]}:
        reasons.append("DISTRIBUTION_LABEL_ABSENT")
    if not rule["requires_live_url"]:
        reasons.append("LIVE_URL_RULE_NOT_REQUIRED")
    if packet["disposition"] != "READY_FOR_HUMAN_SUBMISSION":
        reasons.append("BASE_SUBMISSION_PACKET_HOLD")

    public_capture = None
    evidence_times = [source_time, rule_time]
    scope = None
    if capture is None:
        reasons.append("LIVE_URL_MISSING")
    else:
        observed = _ts(capture["observed_at"], "live_capture.observed_at")
        evidence_times.append(observed)
        if issued_time < observed:
            raise DistributionFulfillmentError("authority predates live capture")
        if observed > now:
            reasons.append("LIVE_CAPTURE_FROM_FUTURE")
        elif (now - observed).total_seconds() > MAX_AGE_SECONDS:
            reasons.append("LIVE_CAPTURE_STALE")
        if observed < source_time:
            reasons.append("LIVE_CAPTURE_PREDATES_SOURCE")
        if observed < rule_time:
            reasons.append("LIVE_CAPTURE_PREDATES_RULE")
        if not 200 <= capture["http_status"] < 400:
            reasons.append("LIVE_URL_NOT_SUCCESSFUL")
        if not capture["publicly_resolvable"]:
            reasons.append("LIVE_URL_NOT_PUBLIC")
        if not any(_host_ok(capture["_live_host"], host) for host in rule["allowed_hosts"]):
            reasons.append("LIVE_URL_HOST_NOT_ALLOWED")
        if not any(_host_ok(capture["_resolved_host"], host) for host in rule["allowed_hosts"]):
            reasons.append("RESOLVED_URL_HOST_NOT_ALLOWED")
        public_capture = {key: value for key, value in capture.items() if not key.startswith("_")}
        scope = {
            "provider_namespace": retained["provider_namespace"],
            "resource_identity": retained["resource_identity"],
            "content_sha256": retained["content_sha256"],
            "capture_verifier": retained["capture_verifier"],
        }

    valid_until = min(timestamp + timedelta(seconds=MAX_AGE_SECONDS) for timestamp in evidence_times)
    core = {
        "schema": RECEIPT_SCHEMA,
        "canonical_source_url": url,
        "advertised_reward": reward_text,
        "evaluated_at": trusted_at,
        "valid_until": _fmt(valid_until),
        "disposition": "READY_FOR_HUMAN_DISTRIBUTION_SUBMISSION" if not reasons else "HOLD",
        "reason_codes": reasons,
        "source": source,
        "maintainer_rule": rule,
        "submission_packet": packet,
        "live_capture": public_capture,
        "capture_scope": scope,
        "authority_record_sha256": _hash(retained),
        "authority": {
            "external_post_performed": False,
            "claim_submission_authorized": False,
            "maintainer_acceptance_inferred": False,
            "payout_inferred": False,
            "revenue_recognized": False,
            "human_submission_review_required": True,
        },
    }
    core["receipt_sha256"] = _hash(core)
    return core


def evaluate_one(
    request: Any,
    *,
    authority: Any,
    trusted_key: bytes,
    trusted_key_id: str,
    trusted_at: Optional[str] = None,
) -> dict[str, Any]:
    trusted_text, _ = _trusted_now(trusted_at)
    return _evaluate(
        request,
        authority=authority,
        trusted_key=trusted_key,
        trusted_key_id=trusted_key_id,
        trusted_at=trusted_text,
    )


def verify_receipt(
    request: Any,
    receipt: Any,
    *,
    authority: Any,
    trusted_key: bytes,
    trusted_key_id: str,
    trusted_at: Optional[str] = None,
) -> bool:
    """Verify immutable decision integrity; require freshness only for READY.

    HOLD receipts remain integrity-verifiable after evidence expiry.  They never
    grant positive authority, and deterministic replay must still reproduce the
    HOLD.  READY receipts are current-authority objects and expire at
    ``valid_until``.
    """
    try:
        if type(receipt) is not dict or receipt.get("schema") != RECEIPT_SCHEMA:
            return False
        receipt_time = _ts(receipt.get("evaluated_at"), "receipt.evaluated_at")
        _, now = _trusted_now(trusted_at)
        if receipt_time > now:
            return False
        expected = _evaluate(
            request,
            authority=authority,
            trusted_key=trusted_key,
            trusted_key_id=trusted_key_id,
            trusted_at=_fmt(receipt_time),
        )
        if not _exact_json(expected, receipt):
            return False
        if receipt["disposition"] == "READY_FOR_HUMAN_DISTRIBUTION_SUBMISSION":
            valid_until = _ts(receipt.get("valid_until"), "receipt.valid_until")
            if now > valid_until:
                return False
        return True
    except (DistributionFulfillmentError, TypeError):
        return False


def _queue_item(receipt: dict[str, Any], extra_reasons: list[str]) -> dict[str, Any]:
    reasons = list(extra_reasons)
    disposition = "HOLD" if reasons or receipt["disposition"] == "HOLD" else "READY_FOR_HUMAN_DISTRIBUTION_SUBMISSION"
    core = {
        "schema": QUEUE_ITEM_SCHEMA,
        "canonical_source_url": receipt["canonical_source_url"],
        "queue_disposition": disposition,
        "queue_reason_codes": reasons,
        "receipt": receipt,
    }
    core["decision_sha256"] = _hash(core)
    return core


def build_queue(
    requests: Any,
    *,
    authorities_by_source: Any,
    trusted_key: bytes,
    trusted_key_id: str,
    trusted_at: Optional[str] = None,
) -> dict[str, Any]:
    if type(requests) is not list or not requests or len(requests) > 512:
        raise DistributionFulfillmentError("requests must be a non-empty bounded list")
    trusted_text, _ = _trusted_now(trusted_at)
    if type(authorities_by_source) is not dict:
        raise DistributionFulfillmentError("authorities_by_source must be an object")
    source_urls = [_issue(request.get("canonical_source_url")) if type(request) is dict else "" for request in requests]
    if len(source_urls) != len(set(source_urls)):
        raise DistributionFulfillmentError("queue contains duplicate canonical sources")
    if set(authorities_by_source) != set(source_urls):
        raise DistributionFulfillmentError("authorities_by_source must exactly cover queue sources")

    receipts = [
        _evaluate(
            request,
            authority=authorities_by_source[source],
            trusted_key=trusted_key,
            trusted_key_id=trusted_key_id,
            trusted_at=trusted_text,
        )
        for request, source in zip(requests, source_urls)
    ]
    extras: dict[str, set[str]] = {source: set() for source in source_urls}

    by_url: dict[str, list[dict[str, Any]]] = {}
    by_resource: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_content: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for receipt in receipts:
        capture = receipt["live_capture"]
        scope = receipt["capture_scope"]
        if capture is None or scope is None:
            continue
        by_url.setdefault(capture["resolved_url"], []).append(receipt)
        by_resource.setdefault((scope["provider_namespace"], scope["resource_identity"]), []).append(receipt)
        by_content.setdefault((scope["provider_namespace"], scope["content_sha256"]), []).append(receipt)

    for mapping, code in (
        (by_url, "LIVE_URL_REUSED_ACROSS_CLAIMS"),
        (by_resource, "LIVE_RESOURCE_REUSED_ACROSS_CLAIMS"),
        (by_content, "LIVE_CONTENT_REUSED_ACROSS_CLAIMS"),
    ):
        for matches in mapping.values():
            if len(matches) > 1:
                for receipt in matches:
                    extras[receipt["canonical_source_url"]].add(code)

    items = [_queue_item(receipt, sorted(extras[receipt["canonical_source_url"]])) for receipt in receipts]
    key = lambda item: (-Decimal(item["receipt"]["advertised_reward"]), item["canonical_source_url"])
    ready = sorted((item for item in items if item["queue_disposition"].startswith("READY")), key=key)
    hold = sorted((item for item in items if item["queue_disposition"] == "HOLD"), key=key)
    core = {
        "schema": QUEUE_SCHEMA,
        "evaluated_at": trusted_text,
        "ready_count": len(ready),
        "hold_count": len(hold),
        "ready": ready,
        "hold": hold,
        "action_queue": [
            {
                "canonical_source_url": item["canonical_source_url"],
                "advertised_reward": item["receipt"]["advertised_reward"],
                "reason_codes": item["receipt"]["reason_codes"] + item["queue_reason_codes"],
                "next_action": "PUBLISH_OR_CAPTURE_LIVE_URL"
                if "LIVE_URL_MISSING" in item["receipt"]["reason_codes"]
                else "REVIEW_HOLD_REASONS",
            }
            for item in hold
        ],
        "authority": {
            "external_post_performed": False,
            "claim_submission_authorized": False,
            "payout_inferred": False,
            "revenue_recognized": False,
        },
    }
    core["queue_sha256"] = _hash(core)
    return core


def verify_queue(
    requests: Any,
    queue: Any,
    *,
    authorities_by_source: Any,
    trusted_key: bytes,
    trusted_key_id: str,
    trusted_at: Optional[str] = None,
) -> bool:
    try:
        if type(queue) is not dict or queue.get("schema") != QUEUE_SCHEMA:
            return False
        queue_time = _ts(queue.get("evaluated_at"), "queue.evaluated_at")
        _, now = _trusted_now(trusted_at)
        if queue_time > now:
            return False
        expected = build_queue(
            requests,
            authorities_by_source=authorities_by_source,
            trusted_key=trusted_key,
            trusted_key_id=trusted_key_id,
            trusted_at=_fmt(queue_time),
        )
        if not _exact_json(expected, queue):
            return False
        request_by_source = {_issue(request["canonical_source_url"]): request for request in requests}
        for item in queue["ready"] + queue["hold"]:
            source = item["canonical_source_url"]
            if source not in request_by_source:
                return False
            if not verify_receipt(
                request_by_source[source],
                item["receipt"],
                authority=authorities_by_source[source],
                trusted_key=trusted_key,
                trusted_key_id=trusted_key_id,
                trusted_at=_fmt(now),
            ):
                return False
        return True
    except (DistributionFulfillmentError, TypeError, KeyError):
        return False


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise DistributionFulfillmentError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _loads(text: str, *, name: str) -> Any:
    if len(text.encode()) > 2 * 1024 * 1024:
        raise DistributionFulfillmentError(f"{name} exceeds 2 MiB")
    try:
        return json.loads(text, object_pairs_hook=_pairs)
    except json.JSONDecodeError as exc:
        raise DistributionFulfillmentError(f"{name} is not valid JSON") from exc


def _load(path: str) -> Any:
    text = __import__("sys").stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return _loads(text, name="input")


def _trusted_parent_fd(parent: Path, *, name: str) -> int:
    """Open one trusted parent generation and retain it for child acquisition.

    The authority store is deliberately POSIX-only: O_NOFOLLOW + dir_fd lets
    the verifier bind reads to one directory generation rather than checking a
    pathname and then reopening attacker-rebindable text.
    """
    if os.name != "posix":
        raise DistributionFulfillmentError(f"{name} retained authority requires POSIX descriptor semantics")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        fd = os.open(parent, flags)
    except OSError as exc:
        raise DistributionFulfillmentError(f"{name} trusted parent cannot be opened safely") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise DistributionFulfillmentError(f"{name} trusted parent must be a directory")
        if info.st_uid not in {0, os.geteuid()}:
            raise DistributionFulfillmentError(f"{name} trusted parent has an untrusted owner")
        if stat.S_IMODE(info.st_mode) & 0o022:
            raise DistributionFulfillmentError(f"{name} trusted parent must not be group/other writable")
        return fd
    except Exception:
        os.close(fd)
        raise


def _read_private_file(path: Path, *, name: str, limit: int = 64 * 1024) -> str:
    parent_fd = _trusted_parent_fd(path.parent, name=name)
    fd = -1
    try:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            fd = os.open(path.name, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise DistributionFulfillmentError(f"{name} must be a non-symlink regular file") from exc
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise DistributionFulfillmentError(f"{name} must be a regular file")
        if info.st_uid not in {0, os.geteuid()}:
            raise DistributionFulfillmentError(f"{name} has an untrusted owner")
        if info.st_size > limit:
            raise DistributionFulfillmentError(f"{name} is too large")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise DistributionFulfillmentError(f"{name} permissions must exclude group/other access")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise DistributionFulfillmentError(f"{name} is too large")
        try:
            return b"".join(chunks).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DistributionFulfillmentError(f"{name} is not UTF-8") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(parent_fd)


def _load_trusted_key(path: Path = DEFAULT_AUTHORITY_KEY_PATH) -> tuple[str, bytes]:
    raw = _loads(_read_private_file(path, name="authority key", limit=4096), name="authority key")
    raw = _obj(raw, {"schema", "key_id", "key_hex"}, "authority key")
    if raw["schema"] != AUTHORITY_KEY_SCHEMA:
        raise DistributionFulfillmentError("authority key schema is unsupported")
    key_id = _text(raw["key_id"], "authority key key_id", 128)
    if not KEY_ID_RE.fullmatch(key_id):
        raise DistributionFulfillmentError("authority key key_id is not canonical")
    key_hex = _text(raw["key_hex"], "authority key key_hex", 64)
    if not HEX_KEY_RE.fullmatch(key_hex):
        raise DistributionFulfillmentError("authority key key_hex must be 64 lowercase hex characters")
    return key_id, bytes.fromhex(key_hex)


def _authority_record_path(source_url: str, root: Path = DEFAULT_AUTHORITY_RECORD_DIR) -> Path:
    source = _issue(source_url)
    digest = hashlib.sha256(source.encode()).hexdigest()
    return root / f"{digest}.json"


def _load_retained_authority(source_url: str, root: Path = DEFAULT_AUTHORITY_RECORD_DIR) -> dict[str, Any]:
    path = _authority_record_path(source_url, root)
    raw = _loads(_read_private_file(path, name="authority record"), name="authority record")
    if type(raw) is not dict:
        raise DistributionFulfillmentError("authority record must be an object")
    return raw


def _authorities_for_requests(requests: Sequence[Any], root: Path = DEFAULT_AUTHORITY_RECORD_DIR) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for request in requests:
        if type(request) is not dict:
            raise DistributionFulfillmentError("request must be an object")
        source = _issue(request.get("canonical_source_url"))
        if source in out:
            raise DistributionFulfillmentError("queue contains duplicate canonical sources")
        out[source] = _load_retained_authority(source, root)
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("evaluate")
    p.add_argument("request")
    p = sub.add_parser("queue")
    p.add_argument("requests")
    p = sub.add_parser("verify")
    p.add_argument("request")
    p.add_argument("receipt")
    p = sub.add_parser("verify-queue")
    p.add_argument("requests")
    p.add_argument("queue")
    args = parser.parse_args(argv)
    try:
        now = _fmt(datetime.now(timezone.utc))
        key_id, key = _load_trusted_key()
        if args.cmd == "evaluate":
            request = _load(args.request)
            source = _issue(request.get("canonical_source_url")) if type(request) is dict else ""
            result = evaluate_one(
                request,
                authority=_load_retained_authority(source),
                trusted_key=key,
                trusted_key_id=key_id,
                trusted_at=now,
            )
        elif args.cmd == "queue":
            requests = _load(args.requests)
            if type(requests) is not list:
                raise DistributionFulfillmentError("requests must be a list")
            result = build_queue(
                requests,
                authorities_by_source=_authorities_for_requests(requests),
                trusted_key=key,
                trusted_key_id=key_id,
                trusted_at=now,
            )
        elif args.cmd == "verify":
            request = _load(args.request)
            source = _issue(request.get("canonical_source_url")) if type(request) is dict else ""
            ok = verify_receipt(
                request,
                _load(args.receipt),
                authority=_load_retained_authority(source),
                trusted_key=key,
                trusted_key_id=key_id,
                trusted_at=now,
            )
            print(json.dumps({"verified": ok}))
            return 0 if ok else 3
        else:
            requests = _load(args.requests)
            if type(requests) is not list:
                raise DistributionFulfillmentError("requests must be a list")
            ok = verify_queue(
                requests,
                _load(args.queue),
                authorities_by_source=_authorities_for_requests(requests),
                trusted_key=key,
                trusted_key_id=key_id,
                trusted_at=now,
            )
            print(json.dumps({"verified": ok}))
            return 0 if ok else 3
    except (DistributionFulfillmentError, OSError, UnicodeError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result.get("disposition", "READY").startswith("READY") or result.get("ready_count", 0) else 3


if __name__ == "__main__":
    raise SystemExit(main())
