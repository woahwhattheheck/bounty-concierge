# SPDX-License-Identifier: MIT
"""Fail-closed readiness gate for bounties requiring live distribution.

The gate is evidence-only: it never posts content, submits a claim, infers
maintainer acceptance, or recognizes payout/revenue.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class DistributionFulfillmentError(ValueError):
    pass


ISSUE_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)$")
COMMENT_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)#issuecomment-([1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
BLOCKED_HOSTS = {"github.com", "www.github.com", "gist.github.com"}
MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


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
    if dt.tzinfo is None or dt.utcoffset() != timezone.utc.utcoffset(dt) or text != canonical:
        raise DistributionFulfillmentError(f"{name} must be canonical UTC second precision")
    return dt


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
    m = ISSUE_RE.fullmatch(text)
    if not m or any(part in {".", ".."} for part in m.groups()[:2]):
        raise DistributionFulfillmentError("canonical_source_url is not canonical")
    return text


def _comment(value: Any, source: str) -> str:
    text = _text(value, "maintainer_rule.comment_url")
    m, src = COMMENT_RE.fullmatch(text), ISSUE_RE.fullmatch(source)
    if not m or not src:
        raise DistributionFulfillmentError("maintainer rule comment URL is not canonical")
    if tuple(x.casefold() for x in m.groups()[:3]) != tuple(x.casefold() for x in src.groups()):
        raise DistributionFulfillmentError("maintainer rule comment must belong to source issue")
    return text


def _host(value: Any, name: str) -> str:
    host = _text(value, name, 253).casefold().rstrip(".")
    labels = host.split(".")
    if len(labels) < 2 or any(not HOST_LABEL_RE.fullmatch(x) for x in labels):
        raise DistributionFulfillmentError(f"{name} is not a canonical hostname")
    return host


def _live_url(value: Any, name: str) -> tuple[str, str]:
    text = _text(value, name)
    parsed = urlsplit(text)
    try:
        port = parsed.port
    except ValueError as exc:
        raise DistributionFulfillmentError(f"{name} has an invalid port") from exc
    if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None or port is not None or not parsed.hostname or parsed.fragment:
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
    checked = [_text(v, "source label", 128) for v in labels]
    folded = [v.casefold() for v in checked]
    if len(folded) != len(set(folded)):
        raise DistributionFulfillmentError("source.labels contains duplicates")
    if _ts(raw["updated_at"], "source.updated_at") > _ts(raw["captured_at"], "source.captured_at"):
        raise DistributionFulfillmentError("source snapshot predates source update")
    return {"canonical_source_url": url, "state": raw["state"], "labels": sorted(checked, key=str.casefold), "updated_at": raw["updated_at"], "captured_at": raw["captured_at"], "snapshot_sha256": _sha(raw["snapshot_sha256"], "source.snapshot_sha256")}


def _rule(raw: Any, url: str) -> dict[str, Any]:
    raw = _obj(raw, {"comment_url", "comment_sha256", "captured_at", "requires_live_url", "allowed_hosts", "rule_text_sha256"}, "maintainer_rule")
    if type(raw["requires_live_url"]) is not bool:
        raise DistributionFulfillmentError("maintainer_rule.requires_live_url must be boolean")
    hosts = raw["allowed_hosts"]
    if type(hosts) is not list or not hosts or len(hosts) > 128:
        raise DistributionFulfillmentError("maintainer_rule.allowed_hosts must be a non-empty bounded list")
    checked = [_host(v, "allowed host") for v in hosts]
    if len(checked) != len(set(checked)):
        raise DistributionFulfillmentError("maintainer_rule.allowed_hosts contains duplicates")
    if any(v in BLOCKED_HOSTS or v.endswith(".github.com") for v in checked):
        raise DistributionFulfillmentError("GitHub cannot satisfy off-platform delivery")
    _ts(raw["captured_at"], "maintainer_rule.captured_at")
    return {"comment_url": _comment(raw["comment_url"], url), "comment_sha256": _sha(raw["comment_sha256"], "maintainer_rule.comment_sha256"), "captured_at": raw["captured_at"], "requires_live_url": raw["requires_live_url"], "allowed_hosts": sorted(checked), "rule_text_sha256": _sha(raw["rule_text_sha256"], "maintainer_rule.rule_text_sha256")}


def _packet(raw: Any, url: str) -> dict[str, Any]:
    raw = _obj(raw, {"schema", "canonical_source_url", "disposition", "packet_sha256", "authority"}, "submission_packet")
    if raw["schema"] != "bounty-submission-packet-item/v1":
        raise DistributionFulfillmentError("submission_packet schema is unsupported")
    if raw["canonical_source_url"] != url:
        raise DistributionFulfillmentError("submission_packet source does not match canonical source")
    if raw["disposition"] not in {"READY_FOR_HUMAN_SUBMISSION", "HOLD"}:
        raise DistributionFulfillmentError("submission_packet disposition is invalid")
    auth = _obj(raw["authority"], {"submission", "acceptance", "payout", "cash_claim"}, "submission_packet.authority")
    if type(auth["cash_claim"]) is not bool or auth["cash_claim"] is not False or auth["submission"] != "human_only" or auth["acceptance"] != "not_inferred" or auth["payout"] != "not_inferred":
        raise DistributionFulfillmentError("submission_packet authority exceeds boundary")
    return {"schema": raw["schema"], "canonical_source_url": url, "disposition": raw["disposition"], "packet_sha256": _sha(raw["packet_sha256"], "submission_packet.packet_sha256"), "authority": auth}


def _capture(raw: Any, url: str) -> dict[str, Any]:
    keys = {"canonical_source_url", "live_url", "resolved_url", "observed_at", "http_status", "publicly_resolvable", "content_sha256", "capture_sha256", "verifier"}
    raw = _obj(raw, keys, "live_capture")
    if raw["canonical_source_url"] != url:
        raise DistributionFulfillmentError("live capture source mismatch")
    live, live_host = _live_url(raw["live_url"], "live_capture.live_url")
    resolved, resolved_host = _live_url(raw["resolved_url"], "live_capture.resolved_url")
    status = raw["http_status"]
    if type(status) is not int or isinstance(status, bool) or not 100 <= status <= 599:
        raise DistributionFulfillmentError("live_capture.http_status is invalid")
    if type(raw["publicly_resolvable"]) is not bool:
        raise DistributionFulfillmentError("live_capture.publicly_resolvable must be boolean")
    _ts(raw["observed_at"], "live_capture.observed_at")
    core = {"canonical_source_url": url, "live_url": live, "resolved_url": resolved, "observed_at": raw["observed_at"], "http_status": status, "publicly_resolvable": raw["publicly_resolvable"], "content_sha256": _sha(raw["content_sha256"], "live_capture.content_sha256"), "verifier": _text(raw["verifier"], "live_capture.verifier", 128)}
    digest = _sha(raw["capture_sha256"], "live_capture.capture_sha256")
    if _hash(core) != digest:
        raise DistributionFulfillmentError("live_capture.capture_sha256 does not bind capture")
    return {**core, "capture_sha256": digest, "_live_host": live_host, "_resolved_host": resolved_host}


def evaluate_one(request: Any, *, evaluated_at: str) -> dict[str, Any]:
    keys = {"schema", "canonical_source_url", "advertised_reward", "source", "maintainer_rule", "submission_packet", "live_capture"}
    request = _obj(request, keys, "request")
    if request["schema"] != "distribution-fulfillment-request/v1":
        raise DistributionFulfillmentError("request schema is unsupported")
    url, now = _issue(request["canonical_source_url"]), _ts(evaluated_at, "evaluated_at")
    reward = _reward(request["advertised_reward"])
    source, rule = _source(request["source"], url), _rule(request["maintainer_rule"], url)
    packet = _packet(request["submission_packet"], url)
    if _ts(source["captured_at"], "source.captured_at") > now or _ts(rule["captured_at"], "maintainer_rule.captured_at") > now:
        raise DistributionFulfillmentError("captured evidence cannot be from future")
    capture = None if request["live_capture"] is None else _capture(request["live_capture"], url)
    reasons: list[str] = []
    if source["state"] != "open": reasons.append("SOURCE_NOT_OPEN")
    if "distribution" not in {v.casefold() for v in source["labels"]}: reasons.append("DISTRIBUTION_LABEL_ABSENT")
    if not rule["requires_live_url"]: reasons.append("LIVE_URL_RULE_NOT_REQUIRED")
    if packet["disposition"] != "READY_FOR_HUMAN_SUBMISSION": reasons.append("BASE_SUBMISSION_PACKET_HOLD")
    public_capture = None
    if capture is None:
        reasons.append("LIVE_URL_MISSING")
    else:
        observed = _ts(capture["observed_at"], "live_capture.observed_at")
        if observed > now: reasons.append("LIVE_CAPTURE_FROM_FUTURE")
        elif (now - observed).total_seconds() > MAX_AGE_SECONDS: reasons.append("LIVE_CAPTURE_STALE")
        if not 200 <= capture["http_status"] < 400: reasons.append("LIVE_URL_NOT_SUCCESSFUL")
        if not capture["publicly_resolvable"]: reasons.append("LIVE_URL_NOT_PUBLIC")
        if not any(_host_ok(capture["_live_host"], h) for h in rule["allowed_hosts"]): reasons.append("LIVE_URL_HOST_NOT_ALLOWED")
        if not any(_host_ok(capture["_resolved_host"], h) for h in rule["allowed_hosts"]): reasons.append("RESOLVED_URL_HOST_NOT_ALLOWED")
        public_capture = {k: v for k, v in capture.items() if not k.startswith("_")}
    core = {"schema": "distribution-fulfillment-receipt/v1", "canonical_source_url": url, "advertised_reward": format(reward, "f"), "evaluated_at": evaluated_at, "disposition": "READY_FOR_HUMAN_DISTRIBUTION_SUBMISSION" if not reasons else "HOLD", "reason_codes": reasons, "source": source, "maintainer_rule": rule, "submission_packet": packet, "live_capture": public_capture, "authority": {"external_post_performed": False, "claim_submission_authorized": False, "maintainer_acceptance_inferred": False, "payout_inferred": False, "revenue_recognized": False, "human_submission_review_required": True}}
    core["receipt_sha256"] = _hash(core)
    return core


def build_queue(requests: Any, *, evaluated_at: str) -> dict[str, Any]:
    if type(requests) is not list or not requests or len(requests) > 512:
        raise DistributionFulfillmentError("requests must be a non-empty bounded list")
    rows = [evaluate_one(v, evaluated_at=evaluated_at) for v in requests]
    sources = [v["canonical_source_url"] for v in rows]
    if len(sources) != len(set(sources)):
        raise DistributionFulfillmentError("queue contains duplicate canonical sources")
    by_url: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["live_capture"] is not None:
            by_url.setdefault(row["live_capture"]["resolved_url"], []).append(row)
    for matches in by_url.values():
        if len(matches) > 1:
            for row in matches:
                row["reason_codes"].append("LIVE_URL_REUSED_ACROSS_CLAIMS")
                row["disposition"] = "HOLD"
                unsigned = dict(row); unsigned.pop("receipt_sha256")
                row["receipt_sha256"] = _hash(unsigned)
    key = lambda r: (-Decimal(r["advertised_reward"]), r["canonical_source_url"])
    ready = sorted((r for r in rows if r["disposition"].startswith("READY")), key=key)
    hold = sorted((r for r in rows if r["disposition"] == "HOLD"), key=key)
    core = {"schema": "distribution-fulfillment-queue/v1", "evaluated_at": evaluated_at, "ready_count": len(ready), "hold_count": len(hold), "ready": ready, "hold": hold, "action_queue": [{"canonical_source_url": r["canonical_source_url"], "advertised_reward": r["advertised_reward"], "reason_codes": r["reason_codes"], "next_action": "PUBLISH_OR_CAPTURE_LIVE_URL" if "LIVE_URL_MISSING" in r["reason_codes"] else "REVIEW_HOLD_REASONS"} for r in hold], "authority": {"external_post_performed": False, "claim_submission_authorized": False, "payout_inferred": False, "revenue_recognized": False}}
    core["queue_sha256"] = _hash(core)
    return core


def verify_receipt(request: Any, receipt: Any, *, evaluated_at: str) -> bool:
    try:
        return type(receipt) is dict and evaluate_one(request, evaluated_at=evaluated_at) == receipt
    except DistributionFulfillmentError:
        return False


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out: raise DistributionFulfillmentError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _load(path: str) -> Any:
    text = __import__("sys").stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    if len(text.encode()) > 2 * 1024 * 1024: raise DistributionFulfillmentError("input exceeds 2 MiB")
    try: return json.loads(text, object_pairs_hook=_pairs)
    except json.JSONDecodeError as exc: raise DistributionFulfillmentError("input is not valid JSON") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in ("evaluate", "queue"):
        p = sub.add_parser(cmd); p.add_argument("request"); p.add_argument("--evaluated-at", required=True)
    p = sub.add_parser("verify"); p.add_argument("request"); p.add_argument("receipt"); p.add_argument("--evaluated-at", required=True)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "evaluate": result = evaluate_one(_load(args.request), evaluated_at=args.evaluated_at)
        elif args.cmd == "queue": result = build_queue(_load(args.request), evaluated_at=args.evaluated_at)
        else:
            ok = verify_receipt(_load(args.request), _load(args.receipt), evaluated_at=args.evaluated_at); print(json.dumps({"verified": ok})); return 0 if ok else 3
    except (DistributionFulfillmentError, OSError, UnicodeError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True)); return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result.get("disposition", "READY").startswith("READY") or result.get("ready_count", 0) else 3


if __name__ == "__main__":
    raise SystemExit(main())
