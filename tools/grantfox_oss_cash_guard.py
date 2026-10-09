#!/usr/bin/env python3
"""Reconcile GrantFox OSS points, merges, cash awards and cash settlement.

Offline by design: reading a GitHub label or bot comment cannot certify money.
This guard never sends a claim, creates a payout, or authorizes new sponsor work.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

SCHEMA = 'grantfox-oss-cash-evidence/v1'
GITHUB_RE = re.compile(r'^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(issues|pull)/([1-9]\d*)$')
REPO_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
ALLOWED = {'github.com', 'grantfox.xyz', 'www.grantfox.xyz', 'contribute.grantfox.xyz',
           'analytics.grantfox.xyz', 'viewer.trustlesswork.com'}

class EvidenceError(ValueError):
    """Invalid evidence; no implied financial award."""


def _obj(value, field):
    if type(value) is not dict:
        raise EvidenceError(f'{field} must be an object')
    return value


def _nonempty(value, field, limit=1024):
    if type(value) is not str or not value or value != value.strip() or len(value) > limit:
        raise EvidenceError(f'{field} must be bounded nonempty text')
    return value


def _utc(value, field):
    raw = _nonempty(value, field, 48)
    try:
        value = datetime.fromisoformat(raw[:-1] + '+00:00' if raw.endswith('Z') else raw)
    except ValueError as e:
        raise EvidenceError(f'{field} must be ISO-8601') from e
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceError(f'{field} requires timezone')
    return value.astimezone(timezone.utc)


def _url(raw, field, *, hosts=ALLOWED, path_required=False, allow_comment_fragment=False):
    raw = _nonempty(raw, field, 2048)
    p = urlsplit(raw)
    if (p.scheme != 'https' or p.netloc != p.hostname or p.hostname not in hosts
            or p.query or (p.fragment and not allow_comment_fragment) or p.username or p.password or p.port
            or any(ord(c) < 33 for c in raw)):
        raise EvidenceError(f'{field} must be an undecorated first-party HTTPS URL')
    if path_required and not p.path.strip('/'):
        raise EvidenceError(f'{field} must link to a particular record')
    return p


def _github(raw, field, *, kind=None, repo=None, comment=False):
    p = _url(raw, field, hosts={'github.com'}, allow_comment_fragment=comment)
    if comment and not re.fullmatch(r'issuecomment-[1-9]\d*', p.fragment):
        raise EvidenceError(f'{field} requires a canonical GitHub comment fragment')
    m = GITHUB_RE.fullmatch(p.path)
    if m is None or (kind and m[3] != kind) or (repo and f'{m[1]}/{m[2]}'.lower() != repo.lower()):
        raise EvidenceError(f'{field} requires matching canonical GitHub {kind or "record"} URL')
    if not comment and p.fragment:
        raise EvidenceError(f'{field} requires undecorated GitHub issue/PR URL')
    return (m[1].lower(), m[2].lower(), m[3], int(m[4]))


def _money(value, field):
    if type(value) is not str or not re.fullmatch(r'(?:0|[1-9]\d{0,8})(?:\.\d{1,2})?', value):
        raise EvidenceError(f'{field} requires literal nonnegative USDC decimal string')
    try:
        return Decimal(value)
    except InvalidOperation as e:
        raise EvidenceError(f'{field} invalid USDC') from e


def assess(packet):
    p = _obj(packet, 'packet')
    if p.get('schema') != SCHEMA:
        raise EvidenceError('unrecognized schema')
    if set(p) - {'schema','as_of','sponsor_repo','original_contributor','merged_pr_urls',
                 'points','cash','collection'}:
        raise EvidenceError('unknown fields (no implied USD/point conversion allowed)')
    now = _utc(p.get('as_of'), 'as_of')
    repo = _nonempty(p.get('sponsor_repo'), 'sponsor_repo', 200)
    if REPO_RE.fullmatch(repo) is None:
        raise EvidenceError('invalid sponsor_repo')
    claimant = _nonempty(p.get('original_contributor'), 'original_contributor', 39)
    if re.fullmatch(r'[A-Za-z0-9-]{1,39}', claimant) is None:
        raise EvidenceError('invalid GitHub contributor')
    prs = p.get('merged_pr_urls')
    if type(prs) is not list or len(prs) > 500:
        raise EvidenceError('merged_pr_urls must be bounded list')
    keys = [_github(item, 'merged_pr_urls[]', repo=repo, kind='pull') for item in prs]
    if len(set(keys)) != len(keys):
        raise EvidenceError('duplicate PRs inflate claimed delivery count')

    points = _obj(p.get('points'), 'points')
    if set(points) - {'cumulative_awarded','award_record_url','award_record_kind'}:
        raise EvidenceError('points record cannot carry a currency value')
    score = points.get('cumulative_awarded')
    if type(score) is not int or not 0 <= score <= 10_000_000:
        raise EvidenceError('points.cumulative_awarded needs nonnegative integer')
    point_link = points.get('award_record_url')
    if point_link is not None:
        _github(point_link, 'points.award_record_url', kind='issues', repo=repo, comment=True)
    if score and point_link is None:
        raise EvidenceError('positive points require a specific award receipt')
    point_kind = points.get('award_record_kind', 'github_bot_comment')
    if point_kind != 'github_bot_comment':
        raise EvidenceError('unsupported award record type')

    cash = _obj(p.get('cash'), 'cash')
    if set(cash) - {'approved_award_usdc','award_record_url','released_payment_usdc',
                    'release_record_url','recipient_verified','sponsor_verified','provider_readback_at'}:
        raise EvidenceError('cash fields must be explicit provider award or release, not points conversion')
    approved = cash.get('approved_award_usdc')
    paid = cash.get('released_payment_usdc')
    award = cash.get('award_record_url')
    release = cash.get('release_record_url')
    if approved is not None:
        approved = _money(approved, 'cash.approved_award_usdc')
        if not award:
            raise EvidenceError('approved cash needs an award record')
        _url(award, 'cash.award_record_url', hosts={'contribute.grantfox.xyz','analytics.grantfox.xyz'}, path_required=True)
    elif award is not None:
        raise EvidenceError('award record requires explicit cash amount')
    if paid is not None:
        paid = _money(paid, 'cash.released_payment_usdc')
        if not release:
            raise EvidenceError('cash release needs a particular escrow receipt')
        _url(release, 'cash.release_record_url', hosts={'viewer.trustlesswork.com'}, path_required=True)
        if cash.get('recipient_verified') is not True or cash.get('sponsor_verified') is not True:
            raise EvidenceError('cash payment cannot be attributed without same payee and payer check')
        readback = _utc(cash.get('provider_readback_at'), 'cash.provider_readback_at')
        if readback > now:
            raise EvidenceError('provider readback cannot be future-dated')
    elif release is not None or cash.get('recipient_verified') is True or cash.get('sponsor_verified') is True or cash.get('provider_readback_at') is not None:
        raise EvidenceError('provider release metadata requires specific cash transfer')

    collection = _obj(p.get('collection'), 'collection')
    if set(collection) - {'request_comment_url','request_sent_at','response_requested_by'}:
        raise EvidenceError('unknown collection fields')
    request = collection.get('request_comment_url')
    sent = collection.get('request_sent_at')
    due = collection.get('response_requested_by')
    if request is not None:
        _github(request,'collection.request_comment_url',repo=repo,kind='issues',comment=True)
        sent = _utc(sent, 'collection.request_sent_at')
        if sent > now:
            raise EvidenceError('request cannot be in the future')
    elif sent is not None or due is not None:
        raise EvidenceError('response date without existing payment request')
    if due is not None:
        due = _utc(due,'collection.response_requested_by')
        if due < sent:
            raise EvidenceError('response requested before request was made')

    if paid is not None:
        status = 'PROVIDER_RELEASE_RECORDED'
        action = 'NO_DUPLICATE_COLLECTION; RECONCILE_ACCOUNT_RECEIPT'
    elif approved is not None:
        status = 'CASH_AWARD_RECORDED_NOT_PAID'
        action = 'COLLECT_APPROVED_CASH_THROUGH_EXISTING_CHANNEL'
    else:
        status = 'POINTS_ONLY_CASH_AWARD_UNVERIFIED' if score else 'CASH_AWARD_UNVERIFIED'
        if request and due and now < due:
            action = 'WAIT_FOR_EXISTING_REQUEST_RESPONSE; NO_DUPLICATE_PING'
        elif request:
            action = 'CHECK_EXISTING_REQUEST_FOR_REPLY; ESCALATE_ONLY_IF_STILL_UNRESOLVED'
        else:
            action = 'RECONCILE_PROGRAM_ELIGIBILITY_AND_REQUEST_FIRST_CASH_DECISION'
    return {
        'schema': SCHEMA, 'sponsor_repo': repo, 'original_contributor': claimant,
        'as_of': now.isoformat().replace('+00:00', 'Z'),
        'merged_pr_count_recorded': len(keys), 'points_awarded_recorded': score,
        'cash_award_usdc_recorded': str(approved) if approved is not None else None,
        'cash_released_usdc_recorded': str(paid) if paid is not None else None,
        'status': status, 'next_action': action,
        'new_unpaid_builds_allowed': False,
        'zero_dollar_inferred': False, 'points_convertible_to_usdc': False,
        'provider_receipt_is_operator_supplied_not_independently_fetched': bool(paid is not None),
        'claim_sent_by_this_tool': False, 'payout_sent_by_this_tool': False,
        'existing_collection_request_url': request,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path, help='operator-gathered first-party evidence JSON')
    parser.add_argument('--output', type=Path, help='optional decision JSON path')
    args = parser.parse_args()
    decision = assess(json.loads(args.evidence.read_text(encoding='utf-8')))
    rendered = json.dumps(decision, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.write_text(rendered, encoding='utf-8')
    else:
        print(rendered, end='')

if __name__ == '__main__':
    main()
