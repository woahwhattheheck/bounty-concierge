# SPDX-License-Identifier: MIT
"""Bind the canonical Commons v2 outbound lease to the local send guard.

Commons ``capability_lease.py`` owns acquisition and the provider-linearizable
Git ref/tag mutex. This module deliberately does not reimplement acquisition.
It consumes a v2 public receipt plus the current worker's private capability,
re-reads the live provider ref/tag, binds that proof to one local operation and
one upstream preflight generation, and only then permits the existing local
``OutboundSingleWriter`` HELD -> SENDING transition.

A lease is only a mutual-exclusion prerequisite. This module does not prove
buyer identity, owner/content approval, route safety, DNR/cooldown state, or
provider send authority, and it never sends a message.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

CANONICAL_LEASE_REPO = "woahwhattheheck/commons"
LEASE_SCHEMA = "outbound-send-lease/v2"
LEASE_RECEIPT_SCHEMA = "outbound-send-lease-receipt/v2"
LEASE_REF_PREFIX = "refs/tags/outbound-lease-v2/"
LOCAL_PREFLIGHT_SCHEMA = "bounty-concierge-global-outbound-preflight/v2"
LOCAL_PROOF_SCHEMA = "bounty-concierge-global-outbound-proof/v2"
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._:@/+\-]{2,191}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_PROVIDER_BODY = 262144


class CapabilityGateError(RuntimeError):
    """Malformed local/lease input or unreadable provider response."""


class CapabilityGateBlocked(CapabilityGateError):
    """The v2 proof-of-possession prerequisite is not proven."""


def _canon(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise CapabilityGateError("value is not canonical-JSON serializable") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canon(value)).hexdigest()


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise CapabilityGateError(f"{field}: expected 40/64 hex object id")
    return value.lower()


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise CapabilityGateError(f"{field}: expected 64 lowercase hex characters")
    return value


def _machine(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or value != value.casefold()
        or _TOKEN_RE.fullmatch(value) is None
    ):
        raise CapabilityGateError(f"{field}: lowercase ASCII machine token required")
    return value


def _display(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not (3 <= len(value) <= 192):
        raise CapabilityGateError(f"{field}: 3..192 ASCII chars required")
    if any(ord(ch) < 0x20 or ch == "\x7f" for ch in value):
        raise CapabilityGateError(f"{field}: control characters forbidden")
    return value


def _rfc3339(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CapabilityGateError(f"{field}: RFC3339 timestamp required")
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise CapabilityGateError(f"{field}: invalid RFC3339 timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise CapabilityGateError(f"{field}: timezone required")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _capability(value: Any) -> str:
    return _sha256(value, "claim_capability")


def _capability_commitment(capability: str) -> str:
    return hashlib.sha256(bytes.fromhex(_capability(capability))).hexdigest()


def local_preflight_material(
    *, operation_key: str, outbound_preflight_sha256: str
) -> dict[str, str]:
    return {
        "schema": LOCAL_PREFLIGHT_SCHEMA,
        "operation_key": _sha256(operation_key, "operation_key"),
        "outbound_preflight_sha256": _sha256(
            outbound_preflight_sha256, "outbound_preflight_sha256"
        ),
    }


def local_preflight_sha256(
    *, operation_key: str, outbound_preflight_sha256: str
) -> str:
    """Digest that a canonical Commons v2 claim must bind for this local send."""
    return _digest(local_preflight_material(
        operation_key=operation_key,
        outbound_preflight_sha256=outbound_preflight_sha256,
    ))


def build_commons_v2_claim(
    *, buyer_scope: str, offer_scope: str, claimant: str, claim_id: str,
    claim_started_at: str, anchor_sha: str, operation_key: str,
    outbound_preflight_sha256: str,
) -> dict[str, str]:
    """Build the exact public claim to hand to Commons v2 acquisition.

    The coordination repository is intentionally not caller-selectable. Buyer
    and offer scope are authority inputs supplied by the already-approved
    outbound preflight; this helper validates/binds them but does not discover
    or independently authenticate their business meaning.
    """
    return {
        "repo": CANONICAL_LEASE_REPO,
        "buyer_scope": _machine(buyer_scope, "buyer_scope"),
        "offer_scope": _machine(offer_scope, "offer_scope"),
        "claimant": _display(claimant, "claimant"),
        "claim_id": _machine(claim_id, "claim_id"),
        "claim_started_at": _rfc3339(claim_started_at, "claim_started_at"),
        "anchor_sha": _sha(anchor_sha, "anchor_sha"),
        "preflight_sha256": local_preflight_sha256(
            operation_key=operation_key,
            outbound_preflight_sha256=outbound_preflight_sha256,
        ),
    }


def _parse_bound_claim(
    raw: Mapping[str, Any], *, operation_key: str,
    outbound_preflight_sha256: str, owner: str,
) -> dict[str, str]:
    expected = {
        "repo", "buyer_scope", "offer_scope", "claimant", "claim_id",
        "claim_started_at", "anchor_sha", "preflight_sha256",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise CapabilityGateBlocked("claim: exact fields required")
    claim = {
        "repo": raw["repo"],
        "buyer_scope": _machine(raw["buyer_scope"], "buyer_scope"),
        "offer_scope": _machine(raw["offer_scope"], "offer_scope"),
        "claimant": _display(raw["claimant"], "claimant"),
        "claim_id": _machine(raw["claim_id"], "claim_id"),
        "claim_started_at": _rfc3339(raw["claim_started_at"], "claim_started_at"),
        "anchor_sha": _sha(raw["anchor_sha"], "anchor_sha"),
        "preflight_sha256": _sha256(raw["preflight_sha256"], "preflight_sha256"),
    }
    if claim["repo"] != CANONICAL_LEASE_REPO:
        raise CapabilityGateBlocked("claim: non-canonical coordination repository")
    if claim["claimant"] != _display(owner, "owner"):
        raise CapabilityGateBlocked("claim: claimant does not match local owner")
    expected_preflight = local_preflight_sha256(
        operation_key=operation_key,
        outbound_preflight_sha256=outbound_preflight_sha256,
    )
    if claim["preflight_sha256"] != expected_preflight:
        raise CapabilityGateBlocked("claim: wrong local operation/preflight generation")
    return claim


def _seam(claim: Mapping[str, str]) -> dict[str, str]:
    return {
        "schema": LEASE_SCHEMA,
        "buyer_scope": claim["buyer_scope"],
        "offer_scope": claim["offer_scope"],
    }


def _lease_ref(claim: Mapping[str, str]) -> str:
    return LEASE_REF_PREFIX + _digest(_seam(claim))


def _tag_name(claim: Mapping[str, str], capability_sha256: str) -> str:
    claimant_hash = hashlib.sha256(claim["claimant"].encode("ascii")).hexdigest()[:16]
    return (
        f"outbound-claim-v2-{_digest(_seam(claim))[:16]}-"
        f"{claimant_hash}-{capability_sha256[:16]}"
    )


def _metadata(claim: Mapping[str, str], capability_sha256: str) -> dict[str, str]:
    return {
        "schema": LEASE_SCHEMA,
        "repo": claim["repo"],
        "buyer_scope": claim["buyer_scope"],
        "offer_scope": claim["offer_scope"],
        "claimant": claim["claimant"],
        "claim_id": claim["claim_id"],
        "claim_started_at": claim["claim_started_at"],
        "anchor_sha": claim["anchor_sha"],
        "preflight_sha256": claim["preflight_sha256"],
        "claim_capability_sha256": capability_sha256,
        "seam_sha256": _digest(_seam(claim)),
    }


_RECEIPT_FIELDS = {
    "schema", "repo", "buyer_scope", "offer_scope", "seam_sha256", "lease_ref",
    "claim_id", "claimant", "claim_started_at", "anchor_sha", "preflight_sha256",
    "claim_capability_sha256", "tag_object_sha", "observed_ref_sha",
    "lease_held_by_claimant", "decision", "reason", "external_send_authorized",
    "receipt_sha256",
}
_METADATA_FIELDS = {
    "schema", "repo", "buyer_scope", "offer_scope", "claimant", "claim_id",
    "claim_started_at", "anchor_sha", "preflight_sha256",
    "claim_capability_sha256", "seam_sha256",
}


def _verify_receipt(
    raw: Mapping[str, Any], claim: Mapping[str, str]
) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != _RECEIPT_FIELDS:
        raise CapabilityGateBlocked("v2 receipt: exact fields required")
    receipt = dict(raw)
    if receipt["schema"] != LEASE_RECEIPT_SCHEMA:
        raise CapabilityGateBlocked("v2 receipt: unsupported schema")
    if receipt["repo"] != CANONICAL_LEASE_REPO:
        raise CapabilityGateBlocked("v2 receipt: non-canonical coordination repository")
    for field in ("buyer_scope", "offer_scope", "claim_id", "claimant", "anchor_sha", "preflight_sha256"):
        if receipt[field] != claim[field]:
            raise CapabilityGateBlocked(f"v2 receipt: {field} does not match bound claim")
    if _rfc3339(receipt["claim_started_at"], "receipt claim_started_at") != claim["claim_started_at"]:
        raise CapabilityGateBlocked("v2 receipt: claim_started_at does not match bound claim")
    seam_sha = _digest(_seam(claim))
    if _sha256(receipt["seam_sha256"], "receipt seam_sha256") != seam_sha:
        raise CapabilityGateBlocked("v2 receipt: seam mismatch")
    if receipt["lease_ref"] != LEASE_REF_PREFIX + seam_sha:
        raise CapabilityGateBlocked("v2 receipt: lease ref mismatch")
    _sha256(receipt["claim_capability_sha256"], "receipt claim_capability_sha256")
    tag_sha = _sha(receipt["tag_object_sha"], "receipt tag_object_sha")
    observed = receipt["observed_ref_sha"]
    if observed is not None:
        _sha(observed, "receipt observed_ref_sha")
    held = receipt["lease_held_by_claimant"]
    if type(held) is not bool:
        raise CapabilityGateBlocked("v2 receipt: invalid lease-held flag")
    if receipt["decision"] != ("LEASE_HELD" if held else "HOLD"):
        raise CapabilityGateBlocked("v2 receipt: decision inconsistent with lease state")
    if not isinstance(receipt["reason"], str) or not receipt["reason"]:
        raise CapabilityGateBlocked("v2 receipt: reason required")
    if receipt["external_send_authorized"] is not False:
        raise CapabilityGateBlocked("v2 receipt: lease may never self-authorize a send")
    if held and observed != tag_sha:
        raise CapabilityGateBlocked("v2 receipt: held lease lacks exact ref observation")
    digest = _sha256(receipt["receipt_sha256"], "receipt receipt_sha256")
    material = dict(receipt)
    material.pop("receipt_sha256")
    if _digest(material) != digest:
        raise CapabilityGateBlocked("v2 receipt: digest mismatch")
    return receipt


def _strict_object(items):
    out: dict[str, Any] = {}
    for key, value in items:
        if key in out:
            raise CapabilityGateBlocked(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _tag_metadata(tag_body: Any) -> Optional[Mapping[str, Any]]:
    if not isinstance(tag_body, Mapping) or not isinstance(tag_body.get("message"), str):
        return None
    try:
        value = json.loads(
            tag_body["message"], object_pairs_hook=_strict_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                CapabilityGateBlocked(f"non-finite JSON number: {token}")
            ),
        )
    except (TypeError, ValueError, json.JSONDecodeError, CapabilityGateBlocked):
        return None
    if not isinstance(value, Mapping) or set(value) != _METADATA_FIELDS:
        return None
    return value


def _object_sha(ref_body: Any) -> Optional[str]:
    # Match the canonical Commons transport contract: authority is the exact
    # ref -> annotated-tag object SHA. GitHub normally includes an object type
    # here, but the v2 primitive intentionally does not depend on that optional
    # response field; the second GET validates the object as an annotated tag.
    if not isinstance(ref_body, Mapping):
        return None
    obj = ref_body.get("object")
    if not isinstance(obj, Mapping):
        return None
    try:
        return _sha(obj.get("sha"), "ref object sha")
    except CapabilityGateError:
        return None


def _verify_possession_bound(
    *, operation_key: str, outbound_preflight_sha256: str, owner: str,
    claim: Mapping[str, Any], lease_receipt: Mapping[str, Any],
    claim_capability: str, transport,
) -> dict[str, Any]:
    """Private testable verifier. Public callers cannot inject ``transport``."""
    parsed = _parse_bound_claim(
        claim,
        operation_key=operation_key,
        outbound_preflight_sha256=outbound_preflight_sha256,
        owner=owner,
    )
    receipt = _verify_receipt(lease_receipt, parsed)

    # Possession is checked before any provider I/O. Copying all public winner
    # evidence is useless without the private capability retained at acquisition.
    capability = _capability(claim_capability)
    commitment = _capability_commitment(capability)
    if commitment != receipt["claim_capability_sha256"]:
        raise CapabilityGateBlocked("v2 receipt: current worker lacks claim capability")

    expected_ref = _lease_ref(parsed)
    owner_name, repo_name = CANONICAL_LEASE_REPO.split("/", 1)
    quoted = urllib.parse.quote(expected_ref.removeprefix("refs/"), safe="/")
    ref_status, ref_body = transport(
        "GET", f"/repos/{owner_name}/{repo_name}/git/ref/{quoted}", None
    )
    if ref_status != 200:
        raise CapabilityGateBlocked(f"v2 lease: live ref read failed ({ref_status})")
    live_tag_sha = _object_sha(ref_body)
    if live_tag_sha is None or live_tag_sha != receipt["tag_object_sha"]:
        raise CapabilityGateBlocked("v2 lease: live ref no longer points to receipt tag")

    tag_status, tag_body = transport(
        "GET", f"/repos/{owner_name}/{repo_name}/git/tags/{live_tag_sha}", None
    )
    if tag_status != 200 or not isinstance(tag_body, Mapping):
        raise CapabilityGateBlocked(f"v2 lease: annotated tag read failed ({tag_status})")
    try:
        if _sha(tag_body.get("sha"), "tag response sha") != live_tag_sha:
            raise CapabilityGateBlocked("v2 lease: tag response SHA mismatch")
    except CapabilityGateError as exc:
        raise CapabilityGateBlocked(str(exc)) from exc

    metadata = _tag_metadata(tag_body)
    if metadata is None:
        raise CapabilityGateBlocked("v2 lease: malformed annotated-tag metadata")
    try:
        normalized = {
            "schema": metadata["schema"],
            "repo": metadata["repo"],
            "buyer_scope": _machine(metadata["buyer_scope"], "tag buyer_scope"),
            "offer_scope": _machine(metadata["offer_scope"], "tag offer_scope"),
            "claimant": _display(metadata["claimant"], "tag claimant"),
            "claim_id": _machine(metadata["claim_id"], "tag claim_id"),
            "claim_started_at": _rfc3339(metadata["claim_started_at"], "tag claim_started_at"),
            "anchor_sha": _sha(metadata["anchor_sha"], "tag anchor_sha"),
            "preflight_sha256": _sha256(metadata["preflight_sha256"], "tag preflight_sha256"),
            "claim_capability_sha256": _sha256(
                metadata["claim_capability_sha256"], "tag claim_capability_sha256"
            ),
            "seam_sha256": _sha256(metadata["seam_sha256"], "tag seam_sha256"),
        }
    except CapabilityGateError as exc:
        raise CapabilityGateBlocked(str(exc)) from exc
    if normalized != _metadata(parsed, commitment):
        raise CapabilityGateBlocked("v2 lease: annotated-tag metadata transplant/drift")
    if tag_body.get("tag") != _tag_name(parsed, commitment):
        raise CapabilityGateBlocked("v2 lease: annotated-tag name mismatch")
    obj = tag_body.get("object")
    if not isinstance(obj, Mapping) or obj.get("type") != "commit":
        raise CapabilityGateBlocked("v2 lease: annotated tag must target a commit")
    if _sha(obj.get("sha"), "tag target sha") != parsed["anchor_sha"]:
        raise CapabilityGateBlocked("v2 lease: anchor target drift")
    tagger = tag_body.get("tagger")
    if not isinstance(tagger, Mapping):
        raise CapabilityGateBlocked("v2 lease: tagger missing")
    if tagger.get("name") != "outbound-send-lease-v2":
        raise CapabilityGateBlocked("v2 lease: tagger name mismatch")
    if tagger.get("email") != "lease@tokenjunkielabs.invalid":
        raise CapabilityGateBlocked("v2 lease: tagger email mismatch")
    if _rfc3339(tagger.get("date"), "tagger date") != parsed["claim_started_at"]:
        raise CapabilityGateBlocked("v2 lease: tagger timestamp mismatch")

    proof = {
        "schema": LOCAL_PROOF_SCHEMA,
        "operation_key": _sha256(operation_key, "operation_key"),
        "outbound_preflight_sha256": _sha256(
            outbound_preflight_sha256, "outbound_preflight_sha256"
        ),
        "claimant": parsed["claimant"],
        "claim_id": parsed["claim_id"],
        "buyer_scope": parsed["buyer_scope"],
        "offer_scope": parsed["offer_scope"],
        "lease_ref": expected_ref,
        "tag_object_sha": live_tag_sha,
        "lease_receipt_sha256": receipt["receipt_sha256"],
        "capability_commitment_sha256": commitment,
        "live_ref_verified": True,
        "live_tag_verified": True,
        "proof_of_possession_verified": True,
        "external_send_authorized": False,
    }
    proof["proof_sha256"] = _digest(proof)
    return proof


class _GitHubReadTransport:
    """Read-only production transport; deliberately not exposed as API input."""

    def __init__(self) -> None:
        self._token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self._context = ssl.create_default_context()

    def __call__(self, method: str, path: str, payload: Any):
        if method != "GET" or payload is not None:
            raise CapabilityGateError("production v2 verifier is read-only")
        request = urllib.request.Request(
            "https://api.github.com" + path,
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "bounty-concierge-capability-gate/1",
                **({"Authorization": f"Bearer {self._token}"} if self._token else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15.0, context=self._context) as response:
                raw = response.read(MAX_PROVIDER_BODY + 1)
                if len(raw) > MAX_PROVIDER_BODY:
                    return 0, {"error": "provider response too large"}
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except (UnicodeError, json.JSONDecodeError):
                    return 0, {"error": "provider response is not JSON"}
                return int(response.status), body
        except urllib.error.HTTPError as exc:
            raw = exc.read(MAX_PROVIDER_BODY + 1)
            if len(raw) > MAX_PROVIDER_BODY:
                return int(exc.code), None
            try:
                body = json.loads(raw.decode("utf-8")) if raw else None
            except (UnicodeError, json.JSONDecodeError):
                body = None
            return int(exc.code), body
        except (OSError, urllib.error.URLError):
            return 0, None


def verify_global_capability_lease(
    *, operation_key: str, outbound_preflight_sha256: str, owner: str,
    claim: Mapping[str, Any], lease_receipt: Mapping[str, Any],
    claim_capability: str,
) -> dict[str, Any]:
    """Verify current-worker v2 possession against live canonical GitHub state."""
    return _verify_possession_bound(
        operation_key=operation_key,
        outbound_preflight_sha256=outbound_preflight_sha256,
        owner=owner,
        claim=claim,
        lease_receipt=lease_receipt,
        claim_capability=claim_capability,
        transport=_GitHubReadTransport(),
    )


def _prepare_send_with_transport(
    guard, *, operation_key: str, lease_id: str, owner: str,
    outbound_preflight_sha256: str, claim: Mapping[str, Any],
    lease_receipt: Mapping[str, Any], claim_capability: str, transport,
) -> dict[str, Any]:
    proof = _verify_possession_bound(
        operation_key=operation_key,
        outbound_preflight_sha256=outbound_preflight_sha256,
        owner=owner,
        claim=claim,
        lease_receipt=lease_receipt,
        claim_capability=claim_capability,
        transport=transport,
    )
    # No caller-provided clock is accepted here. The existing local guard owns
    # its transition timing and validates the lease_id/owner against local state.
    send_state = guard.prepare_send(
        operation_key=operation_key,
        lease_id=lease_id,
        owner=owner,
    )
    return {
        "global_lease_proof": proof,
        "send_state": send_state,
        "external_send_authorized": False,
    }


def prepare_send_with_capability_lease(
    guard, *, operation_key: str, lease_id: str, owner: str,
    outbound_preflight_sha256: str, claim: Mapping[str, Any],
    lease_receipt: Mapping[str, Any], claim_capability: str,
) -> dict[str, Any]:
    """Arm local SENDING only after live v2 possession proof.

    The returned ``external_send_authorized`` is always false: callers still
    need the independently approved content/route/dedupe/provider gates before
    performing an external mutation.
    """
    return _prepare_send_with_transport(
        guard,
        operation_key=operation_key,
        lease_id=lease_id,
        owner=owner,
        outbound_preflight_sha256=outbound_preflight_sha256,
        claim=claim,
        lease_receipt=lease_receipt,
        claim_capability=claim_capability,
        transport=_GitHubReadTransport(),
    )
