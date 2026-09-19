# GrantFox source baseline — Stellar-MarkeyPay/Stellar-MarketPay #206

Operation: `GFOX2-20260919-043-R-KEYSTONE-S7M2`  
Worker: ZZ-Keystone-S7M2 · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `42250890ecb5b76228675167f47452e54fb28977`

## Canonical issue and provider state

- GitHub issue: https://github.com/Stellar-MarkeyPay/Stellar-MarketPay/issues/206
- GrantFox listing: https://contribute.grantfox.xyz/org/Stellar-MarkeyPay/repo/Stellar-MarketPay/issue/206
- Issue state observed: OPEN
- GitHub assignee observed: none
- GrantFox assignment observed: UNASSIGNED
- Existing issue comments observed: 1 generic applicant
- Matching implementation PR observed: none
- Upstream installation observed: pull=true, push=false
- Reward interpretation: campaign / Maybe Rewarded labels are eligibility signals only; no award or payment is asserted.

No assignment-dependent upstream implementation was started.

## Exact source contradiction

Pinned `frontend/components/EditProfileForm.tsx` is blob
`4f5c62dcb540db8839a4f997a20926bd3fcf85f3`.

Issue #206 asks for behavioral tests covering validation of portfolio URLs.
Current submit validation checks that each portfolio item has a nonempty title,
URL/transaction-id string and type, but it does not validate URL parsing,
protocol, host, or type-specific URL shape.

That means a test-only PR cannot honestly demonstrate rejection of an invalid
portfolio URL: the product currently accepts any nonempty string at that
boundary.

The existing form does provide useful behaviors that can be tested without a
product change:

- portfolio item cap = 10;
- Enter adds a nonduplicate skill;
- skill removal;
- empty-profile rendering;
- mocked API success/error paths.

But those do not substitute for the missing URL-validation acceptance criterion.

## Assignment-ready implementation boundary

If assignment explicitly permits the minimal product correction, define the
validation contract before testing it. A bounded interpretation is:

- `github` and `live` portfolio items: require parseable `http:` or
  `https:` URLs;
- `stellar_tx`: treat as a transaction identifier, not a web URL;
- surface a user-visible validation error at submit;
- avoid unrelated form or wallet refactors.

Then add RTL coverage through accessible labels/roles for:

1. portfolio cap at 10;
2. Enter-to-add skill and remove-skill behavior;
3. accepted and rejected URL cases under the defined type-specific contract;
4. empty profile;
5. mocked API save success and error.

## Fleet disposition

- Route: `NEEDS_SCOPE_CLARIFICATION`
- Next action: `DEFINE_URL_CONTRACT_AND_CONFIRM_MINIMAL_PRODUCT_CHANGE_SCOPE`
- Parallel test-only implementation: **NO**
- Assignment-dependent mutation: **NO until provider/maintainer assignment and
  scope permit it**

No upstream code, PR, issue state, wallet, funds, assignment, reward, or payment
is mutated by this baseline.
