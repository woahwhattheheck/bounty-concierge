# SPDX-License-Identifier: MIT
"""Fail-closed readiness gate for bounties requiring live distribution.

The gate is evidence-only. It never posts content, submits a claim, infers
maintainer acceptance, authorizes payout, or recognizes revenue.

Authority-driving evidence is supplied in two planes:
* ``request`` contains the auditable business evidence to evaluate;
* ``bindings`` is retained out-of-band from independently verified upstream
  evidence/capture and commits to the exact decision projections consumed here.

The CLI captures wall-clock UTC itself. Library callers may inject ``trusted_at``
only from a trusted clock boundary (tests do this deliberately).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit


class DistributionFulfillmentError(ValueError):
    pass


ISSUE_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)$")
COMMENT_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)#issuecomment-([1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511}$")
BLOCKED_HOSTS = {"github.com", "www.github.com", "gist.github.com"}
MAX_AGE_SECONDS = 7 * 24 * 60 * 60
REQUEST_SCHEMA = "distribution-fulfillment-request/v1"
RECEIPT_SCHEMA = "distribution-fulfillment-receipt/v2"
AUTHORITY_SCHEMA = "distribution-fulfillment-authority/v1"
QUEUE_SCHEMA = "distribution-fulfillment-queue/v2"
QUEUE_ITEM_SCHEMA = "distribution-fulfillment-queue-item/v1"


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DistributionFulfillmentError("value is not canonical JSON") from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _exact_json(left: Any, right: Any) -> bool:
    """Type-sensitive JSON equality (unlike Python bool/int/float equality)."""
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


def _reward(value: Any) -> Decimal:
    if type(value) is not str or not value or len(value) > 128:
        raise DistributionFulfillmentError("advertised_reward must be a bounded decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise DistributionFulfillmentError("advertised_reward must be a decimal string") from exc
    if not parsed.is_finite() or parsed < 0 or len(parsed.as_tuple().digits) > 64:
        raise DistributionFulfillmentError("advertised_reward must be finite, non-negative, and bounded")
    return parsed


def _issue(value: Any) -> str:
    text = _text(value, "canonical_source_url")
    match = ISSUE_RE.fullmatch(text)
    if not match or any(part in {".", ".."} for part in match.groups()[:2]):
        raise DistributionFulfillmentError("canonical_source_url is not canonical")
    return text


def _comment(value: Any, source: str) -> str:
    text = _text(value, "maintainer_rule.comment_url")
    match, src = COMMENT_RE.fullmatch(text), ISSUE_RE.fullmatch(source)
    if not match or not src:
        raise DistributionFulfillmentError("maintainer rule comment URL is not canonical")
    if tuple(x.casefold() for x in match.groups()[:3]) != tuple(x.casefold() for x in src.groups()):
        raise DistributionFulfillmentError("maintainer rule comment must belong to source issue")
    return text


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
    if raw["canonical_source_url"] != url:
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
    if raw["canonical_source_url"] != url:
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
    if _hash(core) != digest:
        raise DistributionFulfillmentError("live_capture.capture_sha256 does not bind capture")
    return {**core, "capture_sha256": digest, "_live_host": live_host, "_resolved_host": resolved_host}


def _bindings_for_fixture(request: Any) -> dict[str, Any]:
    """Test-only constructor for binding fixtures.

    Production code MUST NOT derive authority bindings from the request being
    evaluated. It must load bindings retained independently from the upstream
    evidence/capture verification boundary.
    """
    request = _obj(
        request,
        {"schema", "canonical_source_url", "advertised_reward", "source", "maintainer_rule", "submission_packet", "live_capture"},
        "request",
    )
    if request["schema"] != REQUEST_SCHEMA:
        raise DistributionFulfillmentError("request schema is unsupported")
    url = _issue(request["canonical_source_url"])
    source = _source(request["source"], url)
    rule = _rule(request["maintainer_rule"], url)
    packet = _packet(request["submission_packet"], url)
    capture = None if request["live_capture"] is None else _capture(request["live_capture"], url)
    public_capture = None if capture is None else {key: value for key, value in capture.items() if not key.startswith("_")}
    return {
        "schema": AUTHORITY_SCHEMA,
        "canonical_source_url": url,
        "source_decision_sha256": _hash(source),
        "maintainer_rule_decision_sha256": _hash(rule),
        "submission_packet_decision_sha256": _hash(packet),
        "live_capture_decision_sha256": None if public_capture is None else _hash(public_capture),
        "capture_verifier": None if public_capture is None else public_capture["verifier"],
    }


def _bindings(
    raw: Any,
    *,
    url: str,
    source: dict[str, Any],
    rule: dict[str, Any],
    packet: dict[str, Any],
    capture: Optional[dict[str, Any]],
) -> dict[str, Any]:
    raw = _obj(
        raw,
        {
            "schema",
            "canonical_source_url",
            "source_decision_sha256",
            "maintainer_rule_decision_sha256",
            "submission_packet_decision_sha256",
            "live_capture_decision_sha256",
            "capture_verifier",
        },
        "bindings",
    )
    if raw["schema"] != AUTHORITY_SCHEMA or raw["canonical_source_url"] != url:
        raise DistributionFulfillmentError("bindings do not match request source")
    public_capture = None if capture is None else {key: value for key, value in capture.items() if not key.startswith("_")}
    expected = {
        "source_decision_sha256": _hash(source),
        "maintainer_rule_decision_sha256": _hash(rule),
        "submission_packet_decision_sha256": _hash(packet),
        "live_capture_decision_sha256": None if public_capture is None else _hash(public_capture),
        "capture_verifier": None if public_capture is None else public_capture["verifier"],
    }
    for key, value in expected.items():
        supplied = raw[key]
        if value is None:
            if supplied is not None:
                raise DistributionFulfillmentError(f"bindings.{key} must be null")
        elif key.endswith("sha256"):
            if _sha(supplied, f"bindings.{key}") != value:
                raise DistributionFulfillmentError(f"bindings.{key} does not bind decision evidence")
        else:
            if type(supplied) is not str or supplied != value:
                raise DistributionFulfillmentError(f"bindings.{key} does not bind capture verifier")
    return dict(raw)


def _evaluate(request: Any, *, trusted_at: str, bindings: Any) -> dict[str, Any]:
    request = _obj(
        request,
        {"schema", "canonical_source_url", "advertised_reward", "source", "maintainer_rule", "submission_packet", "live_capture"},
        "request",
    )
    if request["schema"] != REQUEST_SCHEMA:
        raise DistributionFulfillmentError("request schema is unsupported")
    url = _issue(request["canonical_source_url"])
    now = _ts(trusted_at, "trusted_at")
    reward = _reward(request["advertised_reward"])
    source = _source(request["source"], url)
    rule = _rule(request["maintainer_rule"], url)
    packet = _packet(request["submission_packet"], url)
    capture = None if request["live_capture"] is None else _capture(request["live_capture"], url)
    authority = _bindings(bindings, url=url, source=source, rule=rule, packet=packet, capture=capture)

    source_time = _ts(source["captured_at"], "source.captured_at")
    rule_time = _ts(rule["captured_at"], "maintainer_rule.captured_at")
    if source_time > now or rule_time > now:
        raise DistributionFulfillmentError("captured evidence cannot be from future")

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
    if capture is None:
        reasons.append("LIVE_URL_MISSING")
    else:
        observed = _ts(capture["observed_at"], "live_capture.observed_at")
        evidence_times.append(observed)
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

    valid_until = min(timestamp + timedelta(seconds=MAX_AGE_SECONDS) for timestamp in evidence_times)
    core = {
        "schema": RECEIPT_SCHEMA,
        "canonical_source_url": url,
        "advertised_reward": format(reward, "f"),
        "evaluated_at": trusted_at,
        "valid_until": _fmt(valid_until),
        "disposition": "READY_FOR_HUMAN_DISTRIBUTION_SUBMISSION" if not reasons else "HOLD",
        "reason_codes": reasons,
        "source": source,
        "maintainer_rule": rule,
        "submission_packet": packet,
        "live_capture": public_capture,
        "authority_bindings_sha256": _hash(authority),
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


def evaluate_one(request: Any, *, bindings: Any, trusted_at: Optional[str] = None) -> dict[str, Any]:
    trusted_text, _ = _trusted_now(trusted_at)
    return _evaluate(request, trusted_at=trusted_text, bindings=bindings)


def verify_receipt(request: Any, receipt: Any, *, bindings: Any, trusted_at: Optional[str] = None) -> bool:
    """Verify creation-time integrity plus consumption-time freshness."""
    try:
        if type(receipt) is not dict or receipt.get("schema") != RECEIPT_SCHEMA:
            return False
        receipt_time = _ts(receipt.get("evaluated_at"), "receipt.evaluated_at")
        trusted_text, now = _trusted_now(trusted_at)
        _ = trusted_text
        if receipt_time > now:
            return False
        valid_until = _ts(receipt.get("valid_until"), "receipt.valid_until")
        if now > valid_until:
            return False
        expected = _evaluate(request, trusted_at=_fmt(receipt_time), bindings=bindings)
        return _exact_json(expected, receipt)
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


def build_queue(requests: Any, *, bindings_by_source: Any, trusted_at: Optional[str] = None) -> dict[str, Any]:
    if type(requests) is not list or not requests or len(requests) > 512:
        raise DistributionFulfillmentError("requests must be a non-empty bounded list")
    trusted_text, _ = _trusted_now(trusted_at)
    if type(bindings_by_source) is not dict:
        raise DistributionFulfillmentError("bindings_by_source must be an object")
    source_urls = [_issue(request.get("canonical_source_url")) if type(request) is dict else "" for request in requests]
    if len(source_urls) != len(set(source_urls)):
        raise DistributionFulfillmentError("queue contains duplicate canonical sources")
    if set(bindings_by_source) != set(source_urls):
        raise DistributionFulfillmentError("bindings_by_source must exactly cover queue sources")

    receipts = [
        _evaluate(request, trusted_at=trusted_text, bindings=bindings_by_source[source])
        for request, source in zip(requests, source_urls)
    ]
    extras: dict[str, set[str]] = {source: set() for source in source_urls}

    by_url: dict[str, list[dict[str, Any]]] = {}
    by_resource: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_content: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for receipt in receipts:
        capture = receipt["live_capture"]
        if capture is None:
            continue
        by_url.setdefault(capture["resolved_url"], []).append(receipt)
        by_resource.setdefault((capture["verifier"], capture["resource_identity"]), []).append(receipt)
        resolved_host = urlsplit(capture["resolved_url"]).hostname or ""
        by_content.setdefault((capture["verifier"], resolved_host.casefold(), capture["content_sha256"]), []).append(receipt)

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


def verify_queue(requests: Any, queue: Any, *, bindings_by_source: Any, trusted_at: Optional[str] = None) -> bool:
    try:
        if type(queue) is not dict or queue.get("schema") != QUEUE_SCHEMA:
            return False
        receipt_time = _ts(queue.get("evaluated_at"), "queue.evaluated_at")
        _, now = _trusted_now(trusted_at)
        if receipt_time > now:
            return False
        expected = build_queue(requests, bindings_by_source=bindings_by_source, trusted_at=_fmt(receipt_time))
        if not _exact_json(expected, queue):
            return False
        for item in queue["ready"] + queue["hold"]:
            if not verify_receipt(
                next(request for request in requests if request["canonical_source_url"] == item["canonical_source_url"]),
                item["receipt"],
                bindings=bindings_by_source[item["canonical_source_url"]],
                trusted_at=_fmt(now),
            ):
                return False
        return True
    except (DistributionFulfillmentError, StopIteration, TypeError):
        return False


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise DistributionFulfillmentError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _load(path: str) -> Any:
    text = __import__("sys").stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    if len(text.encode()) > 2 * 1024 * 1024:
        raise DistributionFulfillmentError("input exceeds 2 MiB")
    try:
        return json.loads(text, object_pairs_hook=_pairs)
    except json.JSONDecodeError as exc:
        raise DistributionFulfillmentError("input is not valid JSON") from exc


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("evaluate")
    p.add_argument("request")
    p.add_argument("authority")
    p = sub.add_parser("queue")
    p.add_argument("request")
    p.add_argument("authority")
    p = sub.add_parser("verify")
    p.add_argument("request")
    p.add_argument("authority")
    p.add_argument("receipt")
    p = sub.add_parser("verify-queue")
    p.add_argument("request")
    p.add_argument("authority")
    p.add_argument("queue")
    args = parser.parse_args(argv)
    try:
        now = _fmt(datetime.now(timezone.utc))
        if args.cmd == "evaluate":
            result = evaluate_one(_load(args.request), bindings=_load(args.authority), trusted_at=now)
        elif args.cmd == "queue":
            result = build_queue(_load(args.request), bindings_by_source=_load(args.authority), trusted_at=now)
        elif args.cmd == "verify":
            ok = verify_receipt(_load(args.request), _load(args.receipt), bindings=_load(args.authority), trusted_at=now)
            print(json.dumps({"verified": ok}))
            return 0 if ok else 3
        else:
            ok = verify_queue(_load(args.request), _load(args.queue), bindings_by_source=_load(args.authority), trusted_at=now)
            print(json.dumps({"verified": ok}))
            return 0 if ok else 3
    except (DistributionFulfillmentError, OSError, UnicodeError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result.get("disposition", "READY").startswith("READY") or result.get("ready_count", 0) else 3


if __name__ == "__main__":
    raise SystemExit(main())
