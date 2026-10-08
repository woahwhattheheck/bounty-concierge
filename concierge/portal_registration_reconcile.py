# SPDX-License-Identifier: MIT
"""Read-only per-PR portal registration check for existing bounty settlement."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from urllib.parse import urlsplit

SCHEMA = "portal-registration-gap/v1"
HOSTS = {"issuehunt": ("oss.issuehunt.io",), "bountyhub": ("bountyhub.dev", "www.bountyhub.dev", "api.bountyhub.dev")}


class RegistrationGapError(ValueError):
    pass


def reconcile(gh, portal, *, now=None):
    """Compare one actual contributor PR with a complete captured portal output."""
    try:
        provider = portal["provider"]
        repo = gh["repo"].lower()
        issue = gh["issue"]
        pr = gh["pr"]
        claimant = gh["claimant"].lower()
        author = gh["author"].lower()
        head = gh["head_sha"]
        created = portal["captured_at"]
        registered = portal["registered_pr_urls"]
        claims = portal["solver_claims"]
        source = portal["source_url"]
    except (KeyError, AttributeError, TypeError) as exc:
        raise RegistrationGapError("missing required snapshot field") from exc
    if provider not in HOSTS or type(repo) is not str or not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", repo):
        raise RegistrationGapError("provider or repository invalid")
    if type(issue) is not int or type(pr) is not int or issue < 1 or pr < 1:
        raise RegistrationGapError("issue and PR must be positive integers")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise RegistrationGapError("head SHA is invalid")
    if not re.fullmatch(r"[a-z0-9-]{1,39}", claimant):
        raise RegistrationGapError("claimant invalid")
    u = urlsplit(source)
    if u.scheme != "https" or u.netloc not in HOSTS[provider] or u.query or u.fragment:
        raise RegistrationGapError("portal source must be first-party HTTPS")
    try:
        captured = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise RegistrationGapError("provider observation time invalid") from exc
    clock = datetime.now(timezone.utc) if now is None else now
    if clock.tzinfo is None:
        raise RegistrationGapError("clock must be timezone-aware")
    age = (clock - captured).total_seconds()
    if type(registered) is not list or type(claims) is not list:
        raise RegistrationGapError("provider outputs incomplete")
    target = "https://github.com/" + repo + "/pull/" + str(pr)
    registered_set = set()
    for url in registered:
        if type(url) is not str or not url.startswith("https://github.com/"):
            raise RegistrationGapError("invalid registered pull URL")
        registered_set.add(url.lower())
    matches = [x for x in claims if type(x) is dict and x.get("pr_url", "").lower() == target and x.get("claimant", "").lower() == claimant]
    if len(matches) > 1:
        raise RegistrationGapError("conflicting provider rows for one claimant")
    key = "portal:" + provider + ":" + repo + "#" + str(issue) + ":" + claimant + ":pr" + str(pr)
    state = "UNKNOWN"
    fresh = portal.get("http_status") == 200 and portal.get("complete") is True and 0 <= age <= 900
    if fresh and author == claimant and gh.get("linked_issue") is True and gh.get("relation") in ("original", "continuation"):
        row = matches[0] if matches else {}
        if row.get("solver_payout_status") == "PAID" and row.get("solver_payment_ref"):
            state = "PAID"
        elif row.get("award_status") == "AWARDED" and row.get("award_ref"):
            state = "AWARDED"
        elif target in registered_set or matches:
            state = "PORTAL_REGISTERED / UNAWARDED"
        else:
            state = "GITHUB_SUBMITTED / PORTAL_NOT_REGISTERED"
    gap = state == "GITHUB_SUBMITTED / PORTAL_NOT_REGISTERED"
    return {"schema": SCHEMA, "key": key, "work_item_id": hashlib.sha256(key.encode()).hexdigest(),
            "status": state, "gap": gap, "claimant": claimant, "pr_url": target,
            "portal_source": source, "captured_at": created,
            "next_action": "Confirm eligibility and register exact PR with existing account operator" if gap else None,
            "authority": {"provider_write": False, "award_verified": False, "paid_verified": False}}
