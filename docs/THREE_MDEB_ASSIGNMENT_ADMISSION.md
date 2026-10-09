# 3mdeb / Dasharo paid-bounty assignment intake

This is a read-only sponsor-specific module for the fleet's MOVA cash-opportunity process. It NEVER applies for, submits, settles, or receives payment for a bounty. A sponsor label alone is not entitlement.

First-party sponsor rules: https://3mdeb.com/bug-bounty/ .
A candidate needs the general bounty label and exactly one recognized tier label (bounty-warmup, bounty-easy, bounty-medium, bounty-hard). Express interest and receive the maintainer assignment BEFORE building. Merged code and paid OpenCollective expense are distinct facts. Tier rewards are indicative, not guaranteed awards.

Run from repo root:

    python -m concierge.three_mdeb_admission snapshot.json --actor woahwhattheheck --as-of 2026-10-09T08:30:00Z

Input must be previously independently verified snapshots, not self-attested claims:
- top-level issues array and paid_merge_history array;
- for each issue: GitHub repository OWNER/REPO, positive number, open/closed state, live labels, current assignees, CURRENT open_prs evidence from GitHub, last_maintainer_action_at from an actual maintainer, and offer from the sponsor itself;
- the offer must have amount >= USD 15, currency USD, confirmed_by_sponsor=true and the original GitHub issue source_url;
- paid_merge_history must contain one SAME sponsor record (sponsor 3mdeb) with merged=true, paid=true, offset-aware paid_at, an original GitHub PR URL, and an actual 3mdeb OpenCollective expense permalink. The adapter validates URL shape, but the source values must be independently checked. Do not treat synthetic fixtures, a merged PR alone or an unpaid promise as sponsor payout history.

Classification:
- REQUEST_ASSIGNMENT: otherwise qualified fresh issue with NO assignment; contact the maintainer through the authorized claimant workflow, and WAIT for real maintainer assignment before building;
- BUILD_READY: actor woahwhattheheck or tokenjunkielabs is itself a listed assignee and there is a matching maintainer_assignment_proof: actor, approved=true, original issue comment permalink https://github.com/OWNER/REPO/issues/N#issuecomment-ID. No already-open competing or owner PR, evidence still fresh <=90 days, hardware_validation_ready=true if hardware_required=true;
- HOLD: invalid or missing evidence, stale maintainer, no confirmed dollar amount, competing PR, other assignee or existing original carrier. Check reasons, preserve original claim/PR, do not reinterpret as green.

Every result explicitly says NO_CLAIM_EMITTED and payout_status NOT_VERIFIED. Source JSON is not signed or authenticated by this tool; validate its canonical provider receipts before allocating actual paid work. Read-only classification does not authorize using another claimant's assignment.

Focused synthetic probe: tests/test_three_mdeb_admission.py. No broad repository validation.
